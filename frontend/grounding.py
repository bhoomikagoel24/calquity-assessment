"""
grounding.py — evidence-grounding ("trust guard") for ParcelPilot Ops Assistant.

WHY THIS EXISTS
----------------
The previous trust guard only checked whether a monetary figure in the final
answer appeared verbatim in the *text* of a cited document. That produced
false positives whenever a figure legitimately came from:

  1. structured tool output (e.g. query_structured_data returning
     shipment_fee_inr = 2400), or
  2. a deterministic calculation over already-grounded numbers
     (e.g. 10% x INR 2400 = INR 240, or "lower of INR 500 or INR 300").

This module classifies every monetary figure in an answer into exactly one
of four buckets:

  - document_grounded              found in the text of a cited source
  - structured_data_grounded       found in structured tool-call results
  - derived_from_grounded_evidence a calculation whose inputs are grounded
  - unverified                     none of the above -> genuinely suspicious

BACKWARD COMPATIBILITY
-----------------------
The original frontend code reads `grounding["all_grounded"]` and
`grounding["unverified"]` (a list of raw number strings). Both keys are
preserved with the same shape so existing callers keep working unmodified.
The additional keys are purely additive.

INTEGRATION
-----------
Wherever the old trust-guard function was invoked (most likely inside
`agent.run_turn`, right after the model's final message is produced), replace
that call with:

    from grounding import assess_grounding
    grounding = assess_grounding(last_msg.content, trace)

`trace` is the same list of {"type": ..., "content": ...} step dicts already
being built for the tool trace / audit log, so no new plumbing is required.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

# Matches INR/Rs/₹ prefixed amounts anywhere in text, e.g. "INR 2,400", "₹300", "Rs. 500.50"
_MONEY_RE = re.compile(r"(?:INR|Rs\.?|₹)\s?([0-9][0-9,]*(?:\.[0-9]+)?)", re.IGNORECASE)

# "10% of INR 2400", "10% × 2400", "10 percent of Rs. 2400"
_PERCENT_OF_RE = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*%?\s*(?:percent)?\s*(?:of|×|x|\*)\s*(?:INR|Rs\.?|₹)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
    re.IGNORECASE,
)

# "lower of INR 500 or INR 300", "min of 500 and 300", "higher of A or B"
_MIN_MAX_RE = re.compile(
    r"(lower|minimum|min|higher|maximum|max)\s+of\s+(?:INR|Rs\.?|₹)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)"
    r"\s*(?:or|,|and)\s*(?:INR|Rs\.?|₹)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
    re.IGNORECASE,
)

_TOLERANCE = 1.0  # rupees; absorbs rounding in the model's own arithmetic
_DOC_TEXT_KEYS = ("content", "text", "snippet", "excerpt", "body", "chunk")


def _norm(value: float) -> str:
    """Canonical string form so '2400', '2400.0' and 2400 all compare equal."""
    return str(int(value)) if float(value).is_integer() else f"{float(value):g}"


def _extract_figures(text: str | list | tuple | dict | None) -> list[str]:
    """All distinct currency-prefixed figures appearing in the answer, normalized."""
    if not text:
        return []

    # Gemini/LangChain can sometimes return structured content
    # instead of a plain string.
    if isinstance(text, (list, tuple)):
        parts = []
        for item in text:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                content = item.get("text") or item.get("content")
                if content:
                    parts.append(str(content))
            else:
                parts.append(str(item))
        text = " ".join(parts)

    elif isinstance(text, dict):
        text = str(
            text.get("text")
            or text.get("content")
            or text
        )

    else:
        text = str(text)

    seen: dict[str, None] = {}

    for m in _MONEY_RE.finditer(text):
        raw = m.group(1).replace(",", "")
        try:
            seen[_norm(float(raw))] = None
        except ValueError:
            continue

    return list(seen.keys())


def _walk_numbers(obj: Any) -> set[str]:
    """Recursively pull every numeric leaf out of a parsed tool-result JSON blob."""
    found: set[str] = set()
    if isinstance(obj, bool):
        return found
    if isinstance(obj, (int, float)):
        found.add(_norm(float(obj)))
    elif isinstance(obj, dict):
        for v in obj.values():
            found |= _walk_numbers(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            found |= _walk_numbers(item)
    return found


def _parsed_tool_payloads(trace: Iterable[dict]) -> list[Any]:
    payloads = []
    for step in trace or []:
        if step.get("type") != "ToolMessage":
            continue
        try:
            payloads.append(json.loads(step["content"]))
        except (TypeError, ValueError, KeyError):
            continue
    return payloads


def _collect_structured_numbers(payloads: list[Any]) -> set[str]:
    """Numeric values from structured tool output (e.g. query_structured_data)."""
    numbers: set[str] = set()
    for parsed in payloads:
        if not isinstance(parsed, dict):
            continue
        # Structured-data tools: everything except the "results"/"confidence_assessment"
        # keys (those belong to the retrieval tool) is fair game.
        for key, val in parsed.items():
            if key in ("results", "confidence_assessment"):
                continue
            numbers |= _walk_numbers(val)
    return numbers


def _collect_document_numbers(payloads: list[Any]) -> set[str]:
    """Numeric values appearing in the *text* of retrieved/cited documents."""
    numbers: set[str] = set()
    for parsed in payloads:
        if not isinstance(parsed, dict):
            continue
        for r in parsed.get("results", []) or []:
            if not isinstance(r, dict):
                continue
            for key in _DOC_TEXT_KEYS:
                if r.get(key):
                    numbers |= {_norm(float(v)) for v in _extract_figures(str(r[key]))
                                if _is_number(v)}
                    # also catch bare (non-currency-prefixed) numbers in doc text,
                    # since agreements often read "...a fixed 300 credit..."
                    numbers |= {_norm(float(n)) for n in re.findall(r"\b\d{2,6}(?:\.\d+)?\b", str(r[key]))}
    return numbers


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _derivable(figure: str, grounded_pool: set[str], answer_text: str) -> bool:
    """True if `figure` is a deterministic calculation over already-grounded numbers."""
    target = float(figure)

    for m in _PERCENT_OF_RE.finditer(answer_text):
        pct = float(m.group(1))
        base = m.group(2).replace(",", "")
        if not _is_number(base):
            continue
        base_val = float(base)
        if _norm(base_val) in grounded_pool and abs(pct / 100 * base_val - target) < _TOLERANCE:
            return True

    for m in _MIN_MAX_RE.finditer(answer_text):
        a, b = m.group(2).replace(",", ""), m.group(3).replace(",", "")
        if not (_is_number(a) and _is_number(b)):
            continue
        a_val, b_val = float(a), float(b)
        if _norm(a_val) in grounded_pool or _norm(b_val) in grounded_pool:
            lo, hi = min(a_val, b_val), max(a_val, b_val)
            if abs(lo - target) < _TOLERANCE or abs(hi - target) < _TOLERANCE:
                return True

    return False


def assess_grounding(
    answer_text: str | list | tuple | dict | None,
    trace: list[dict] | None,
) -> dict:
    """
    Classify every monetary figure in `answer_text` using both the cited
    document text and structured tool evidence found in `trace`.
    """

    trace = trace or []

    # Normalize model content before passing it to regex-based functions.
    if isinstance(answer_text, (list, tuple)):
        parts = []
        for item in answer_text:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                content = item.get("text") or item.get("content")
                if content:
                    parts.append(str(content))
            else:
                parts.append(str(item))
        answer_text = " ".join(parts)

    elif isinstance(answer_text, dict):
        answer_text = str(
            answer_text.get("text")
            or answer_text.get("content")
            or answer_text
        )

    elif answer_text is None:
        answer_text = ""

    else:
        answer_text = str(answer_text)

    figures = _extract_figures(answer_text)

    payloads = _parsed_tool_payloads(trace)
    doc_numbers = _collect_document_numbers(payloads)
    structured_numbers = _collect_structured_numbers(payloads)
    grounded_pool = doc_numbers | structured_numbers

    document_grounded: list[str] = []
    structured_data_grounded: list[str] = []
    derived: list[str] = []
    unverified: list[str] = []

    for fig in figures:
        if fig in doc_numbers:
            document_grounded.append(fig)
        elif fig in structured_numbers:
            structured_data_grounded.append(fig)
        elif _derivable(fig, grounded_pool, answer_text):
            derived.append(fig)
        else:
            unverified.append(fig)

    return {
        "all_grounded": len(unverified) == 0,
        "unverified": unverified,
        "document_grounded": document_grounded,
        "structured_data_grounded": structured_data_grounded,
        "derived_from_grounded_evidence": derived,
        "figures_checked": figures,
    }