"""
Problem 1: Proactive Issue Detection.

Builds a ranked view of tickets that deserve ops attention, instead of
waiting for someone to ask. Ranking combines:
  - SLA breach proximity (open tickets close to/past their target)
  - how many accounts are hitting a similar/same issue (recurrence)
  - account tier weight (Enterprise/premium accounts weighted higher)

This is intentionally a simple, explainable scoring function rather than
a black-box model — for an ops triage tool, "why is this ranked #1"
needs to be answerable in one sentence.
"""

import json
import re
from datetime import datetime
import db


def re_findall_words(text: str) -> list:
    return re.findall(r"[a-z]+", text)

TIER_WEIGHT = {"Enterprise": 3, "Growth": 2, "Standard": 1}

# rough SLA targets in hours, keyed by plan + severity proxy — used only
# to compute a *relative* urgency score for triage ranking, not as the
# authoritative policy answer (that always comes from the documents via
# the agent's search_documents tool)
DEFAULT_P1_HOURS = {"Enterprise": 0.5, "Growth": 2, "Standard": 4}


def _hours_since(ts_str: str, snapshot: datetime) -> float:
    try:
        ts = datetime.fromisoformat(str(ts_str))
        return (snapshot - ts).total_seconds() / 3600
    except Exception:
        return 0.0


def _classify_severity(subject: str, description: str) -> str:
    text = f"{subject} {description}".lower()
    if any(k in text for k in ["outage", "all shipment", "down", "security", "credential", "exposed", "exposure", "breach", "api key"]):
        return "P1"
    if any(k in text for k in ["fail", "error", "not working", "delay", "still shows"]):
        return "P2"
    return "P3"


def compute_urgent_tickets(ctx: db.UserContext, snapshot_override: str | None = None) -> list[dict]:
    tickets = db.get_all_tickets_internal(ctx)
    snapshot_str = snapshot_override or db.get_snapshot_time()
    snapshot = datetime.fromisoformat(snapshot_str.split(" Asia")[0].strip())

    # Recurrence: how many OPEN tickets share meaningful subject/description
    # keywords with this one, regardless of account — this is what surfaces
    # "issue affecting multiple customers at once" per the assessment's
    # example. Uses a stopword-filtered word-overlap (Jaccard-style) rather
    # than matching only the first long word, so e.g. "Bulk upload fails
    # for CSV" and "CSV bulk import failing" would count as related even
    # though their first significant word differs.
    STOPWORDS = {"the", "and", "for", "with", "this", "that", "does", "still",
                 "after", "when", "what", "should", "would", "could", "have",
                 "shows", "possible"}

    def keywords(t: dict) -> set:
        text = f"{t['subject']} {t.get('description', '')}".lower()
        words = re_findall_words(text)
        return {w for w in words if len(w) > 3 and w not in STOPWORDS}

    open_tickets = [t for t in tickets if t["status"] in ("open", "OPEN", "in_progress", "investigating")]
    ticket_keywords = {t["ticket_id"]: keywords(t) for t in open_tickets}

    def related_count(ticket_id: str) -> int:
        base = ticket_keywords[ticket_id]
        count = 0
        for other_id, other_kw in ticket_keywords.items():
            if other_id == ticket_id:
                continue
            overlap = base & other_kw
            union = base | other_kw
            jaccard = len(overlap) / len(union) if union else 0
            if jaccard >= 0.15:  # loose threshold — same underlying issue, different wording
                count += 1
        return count + 1  # include itself

    scored = []
    for t in open_tickets:
        hours_open = _hours_since(t["created_at"], snapshot)
        severity = _classify_severity(t["subject"], t.get("description", ""))
        target = DEFAULT_P1_HOURS.get(t["plan"], 4) if severity == "P1" else DEFAULT_P1_HOURS.get(t["plan"], 4) * 4
        breach_ratio = hours_open / target if target else 0
        tier_weight = TIER_WEIGHT.get(t["plan"], 1)
        recurrence = related_count(t["ticket_id"])

        urgency_score = round(breach_ratio * tier_weight * (1 + 0.5 * (recurrence - 1)), 2)

        scored.append({
            "ticket_id": t["ticket_id"],
            "account_name": t["account_name"],
            "plan": t["plan"],
            "subject": t["subject"],
            "estimated_severity": severity,
            "hours_open": round(hours_open, 1),
            "sla_breach_ratio": round(breach_ratio, 2),
            "recurrence_count_similar": recurrence,
            "urgency_score": urgency_score,
        })

    scored.sort(key=lambda x: x["urgency_score"], reverse=True)
    return scored


if __name__ == "__main__":
    ctx = db.UserContext(user_id="ops1", role="ops")
    result = compute_urgent_tickets(ctx)
    print(json.dumps(result, indent=2))
