"""
Evidence handling for ParcelPilot.

This module creates ONE normalized evidence packet per agent turn.

Important distinction:
- retrieval_confidence = confidence in the retrieval result
- evidence_sources = unique logical documents surfaced during retrieval
- source_texts = raw retrieved text used by deterministic grounding checks

This module does NOT claim that retrieval confidence equals answer confidence.
"""

from __future__ import annotations

import json
from typing import Any


def _safe_json(content: Any) -> dict:
    if isinstance(content, dict):
        return content

    if not isinstance(content, str):
        return {}

    try:
        value = json.loads(content)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def collect_retrieval_events(messages: list) -> list[dict]:
    """
    Extract every search_documents result from the agent trace.

    This preserves the raw retrieval events internally, while later
    functions normalize/deduplicate them for audit/UI purposes.
    """
    events = []

    for message in messages:
        if type(message).__name__ != "ToolMessage":
            continue

        if getattr(message, "name", None) != "search_documents":
            continue

        payload = _safe_json(getattr(message, "content", ""))

        results = payload.get("results", [])
        confidence = payload.get("confidence_assessment", {})

        if not isinstance(results, list):
            results = []

        events.append({
            "results": results,
            "confidence_assessment": (
                confidence if isinstance(confidence, dict) else {}
            ),
        })

    return events


def _source_key(result: dict) -> tuple:
    """
    Logical source identity.

    Multiple chunks from the same PDF/source should appear as ONE source
    in the audit/UI.
    """
    return (
        result.get("source")
        or result.get("title")
        or result.get("source_file")
        or "unknown",
        result.get("authority_level"),
        result.get("status"),
        result.get("account_id"),
    )


def normalize_sources(events: list[dict]) -> list[dict]:
    """
    Deduplicate document chunks into logical evidence sources.

    We keep the strongest retrieval metadata seen for each source.
    """
    by_key: dict[tuple, dict] = {}

    for event in events:
        for result in event.get("results", []):
            if not isinstance(result, dict):
                continue

            key = _source_key(result)

            existing = by_key.get(key)

            if existing is None:
                by_key[key] = {
                    "source": result.get("source")
                    or result.get("title")
                    or result.get("source_file")
                    or "unknown",
                    "authority_level": result.get("authority_level"),
                    "status": result.get("status"),
                    "account_id": result.get("account_id"),
                    "doc_type": result.get("doc_type"),
                    "relevance_score": result.get("relevance_score", 0),
                    "chunks_retrieved": 1,
                    "texts": [
                        result.get("text", "")
                    ] if result.get("text") else [],
                }
                continue

            existing["chunks_retrieved"] += 1

            score = result.get("relevance_score", 0) or 0
            if score > (existing.get("relevance_score", 0) or 0):
                existing["relevance_score"] = score

            text = result.get("text", "")
            if text and text not in existing["texts"]:
                existing["texts"].append(text)

    sources = list(by_key.values())

    # Highest authority first, then strongest relevance.
    sources.sort(
        key=lambda x: (
            x["authority_level"]
            if x["authority_level"] is not None
            else 999,
            -(x["relevance_score"] or 0),
        )
    )

    return sources


def _select_retrieval_confidence(events: list[dict]) -> dict:
    """
    Preserve the retrieval system's confidence signal without pretending
    it is the final answer confidence.

    We use the strongest/most informative confidence observed for the turn,
    while preserving the reasons.
    """
    levels = {"low": 0, "medium": 1, "high": 2}

    assessments = []

    for event in events:
        assessment = event.get("confidence_assessment", {})

        confidence = assessment.get("confidence")
        reason = assessment.get("reason")

        if confidence in levels:
            assessments.append({
                "confidence": confidence,
                "reason": reason or "",
            })

    if not assessments:
        return {
            "level": "low",
            "reason": "No retrieval confidence assessment was returned.",
            "assessments": [],
        }

    # Do not simply use the final model message's confidence.
    # This is explicitly retrieval confidence.
    strongest = max(
        assessments,
        key=lambda x: levels[x["confidence"]],
    )

    return {
        "level": strongest["confidence"],
        "reason": strongest["reason"],
        "assessments": assessments,
    }


def build_evidence_packet(messages: list) -> dict:
    """
    Build the single normalized evidence representation for one turn.
    """
    events = collect_retrieval_events(messages)
    sources = normalize_sources(events)

    source_texts = []

    for source in sources:
        for text in source.get("texts", []):
            if text and text not in source_texts:
                source_texts.append(text)

    retrieval_confidence = _select_retrieval_confidence(events)

    return {
        "retrieval_events": len(events),
        "sources": sources,
        "source_texts": source_texts,
        "retrieval_confidence": retrieval_confidence,
    }


def audit_sources(packet: dict) -> list[dict]:
    """
    Compact source representation for audit/UI.

    We intentionally do not dump every retrieved chunk into the audit log.
    """
    return [
        {
            "source": source["source"],
            "authority_level": source["authority_level"],
            "status": source["status"],
            "account_id": source["account_id"],
            "doc_type": source["doc_type"],
            "relevance_score": source["relevance_score"],
            "chunks_retrieved": source["chunks_retrieved"],
        }
        for source in packet.get("sources", [])
    ]


def source_texts(packet: dict) -> list[str]:
    """
    Text corpus for deterministic grounding.
    """
    return packet.get("source_texts", [])