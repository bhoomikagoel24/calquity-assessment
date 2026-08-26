"""
markdown_safety.py — defensive Markdown cleanup for LLM-generated answers.

The agent's system prompt (see backend/SYSTEM_PROMPT_ADDENDUM.md) instructs the
model to never emit raw HTML and to only use well-formed Markdown tables. This
module is the safety net for when it doesn't comply anyway:

  - <br>, <br/>, <br /> -> newline
  - any other stray HTML tag -> stripped (text content kept)
  - a "table" whose second row isn't a valid separator (e.g. the literal
    "| StepEvidence & Reasoning |" bug) -> rendered as a bullet list instead
    of a broken table
  - a real table with a valid separator but ragged row lengths -> padded /
    trimmed so every row has the same number of cells
"""

from __future__ import annotations

import re

_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_SEP_CELL_RE = re.compile(r":?-{2,}:?")


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_separator_row(row_cells: list[str]) -> bool:
    return len(row_cells) > 0 and all(_SEP_CELL_RE.fullmatch(c) for c in row_cells if c != "")


def _repair_table_block(block: list[str]) -> list[str]:
    if len(block) < 2:
        # A single stray "| ... |" line isn't a table at all — surface it as a bullet
        cells = [c for c in _cells(block[0]) if c]
        return [f"- {' — '.join(cells)}"] if cells else []

    header_cells = _cells(block[0])
    sep_cells = _cells(block[1])

    if not (_is_separator_row(sep_cells) and len(sep_cells) == len(header_cells)):
        # Malformed table (e.g. missing/garbled separator row) — degrade gracefully
        # to a bullet list rather than let Streamlit render a broken grid.
        out = []
        for row in block:
            row_cells = [c for c in _cells(row) if c]
            if row_cells:
                out.append(f"- {' — '.join(row_cells)}")
        return out

    n = len(header_cells)
    fixed = [block[0], block[1]]
    for row in block[2:]:
        row_cells = _cells(row)
        if len(row_cells) < n:
            row_cells += [""] * (n - len(row_cells))
        elif len(row_cells) > n:
            row_cells = row_cells[:n]
        fixed.append("| " + " | ".join(row_cells) + " |")
    return fixed


def clean_llm_markdown(text: str) -> str:
    """Sanitize and repair LLM-generated Markdown so it always renders cleanly."""
    if not text:
        return text

    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)

    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("|") and stripped.count("|") >= 2:
            block = []
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                block.append(lines[j])
                j += 1
            out.extend(_repair_table_block(block))
            i = j
        else:
            out.append(line)
            i += 1

    # Collapse 3+ consecutive blank lines down to at most one, for tidier output
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    return cleaned.strip()