"""
Audit logging — the single most important addition for a financial-
services support tool that a demo-grade chatbot usually skips.

Why this matters for CalQuity specifically: their customers are financial
institutions, who will ask "can you show me why the agent told a customer
X" during any compliance review, incident investigation, or dispute. An
answer with no durable record of which sources were consulted, what
confidence the system had, and who confirmed any action taken is not
production-viable in this domain — regardless of how good the reasoning is.

Design choices, deliberately kept simple for this scope:
- Append-only JSONL (one JSON object per line). Cheap to write, greppable,
  trivially portable to a real log pipeline (Datadog/CloudWatch/etc.) later
  — this is the standard shape structured logs take before they get shipped
  to a log aggregator, so the migration path is "point a shipper at the file."
- One record per completed turn: what was asked, which tools fired, which
  document sources were cited and at what authority/confidence level, and
  what (if any) state-changing action was drafted or confirmed.
- No raw customer PII beyond account/order/ticket IDs already in the source
  data — logs reference IDs, not full record dumps, so the audit trail
  itself doesn't become a second place sensitive data leaks from.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

AUDIT_LOG_PATH = Path(__file__).parent / "audit_log.jsonl"


def log_turn(
    user_id: str,
    role: str,
    query: str,
    tools_used: list[str],
    sources_cited: list[dict],
    confidence_levels: list[str],
    escalation_drafted: dict | None,
    escalation_confirmed: bool,
    final_answer_preview: str,
):
    """One audit record per completed agent turn."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "role": role,
        "query": query,
        "tools_used": tools_used,
        "sources_cited": [
            {"source": s.get("source"), "authority_level": s.get("authority_level")}
            for s in sources_cited
        ],
        "confidence_levels_seen": confidence_levels,
        "escalation_drafted": escalation_drafted,
        "escalation_confirmed": escalation_confirmed,
        "final_answer_preview": final_answer_preview,
        # "final_answer_preview": final_answer_preview[:300],
    }
    with open(AUDIT_LOG_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def read_recent(limit: int = 50) -> list[dict]:
    if not AUDIT_LOG_PATH.exists():
        return []
    lines = AUDIT_LOG_PATH.read_text().strip().split("\n")
    lines = [l for l in lines if l.strip()]
    records = [json.loads(l) for l in lines[-limit:]]
    return list(reversed(records))


def escalations_missing_confirmation() -> list[dict]:
    """Compliance check: any drafted escalation that was never confirmed
    one way or the other is worth surfacing — it means a flagged issue
    may have silently fallen through."""
    all_records = read_recent(limit=10_000)
    return [r for r in all_records if r["escalation_drafted"] and not r["escalation_confirmed"]]


if __name__ == "__main__":
    log_turn(
        user_id="ops1", role="ops", query="test query",
        tools_used=["search_documents"],
        sources_cited=[{"source": "Northstar Agreement", "authority_level": 1}],
        confidence_levels=["high"],
        escalation_drafted=None, escalation_confirmed=False,
        final_answer_preview="Test answer preview.",
    )
    print(read_recent(5))
