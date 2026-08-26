"""
The 3 required agent tools, wrapped as LangChain @tool functions so
they can be bound directly to a LangGraph agent.

1. search_documents        -> document search/retrieval (ChromaDB)
2. query_structured_data    -> account/order/ticket lookup + calc (SQLite)
3. create_escalation         -> mocked state-changing action, confirmation
                                 is enforced in agent.py (interrupt), not here
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from langchain_core.tools import tool

import db
from ingest import load_vector_store, expand_query, assess_confidence

_collection = None
ESCALATIONS_PATH = Path(__file__).parent / "escalations.json"


def _get_collection():
    global _collection
    if _collection is None:
        _collection = load_vector_store()
    return _collection


def make_tools(ctx: "db.UserContext"):
    """Tools are built per-request, closed over the caller's UserContext,
    so access control is enforced by the tool itself and cannot be
    bypassed by prompt instructions."""

    @tool
    def search_documents(query: str) -> str:
        """Search ParcelPilot's policy documents, SOPs, product ops guide,
        and customer agreements. Returns matching clauses along with each
        source's authority level (1=customer agreement, highest, through
        5=deprecated, never use to answer), status (current/deprecated),
        and a confidence assessment. Always prefer higher-authority (lower
        number), current sources. Deprecated sources are returned only so
        you can explicitly note that you are NOT using them. If confidence
        is 'low' or 'medium', say so in your answer rather than presenting
        it as certain — consider whether the question needs escalation."""
        col = _get_collection()
        expanded = expand_query(query)
        res = col.query(query_texts=[expanded], n_results=5)
        out = []
        # for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        #     out.append({
        #         "source": meta["title"],
        #         "authority_level": meta["authority_level"],
        #         "status": meta["status"],
        #         "applies_to_account": meta["account_id"],
        #         "text": doc,
        #         "relevance_score": round(1 - dist, 3),
        #     })
        for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
            score = round(1 - dist, 3)

            if score < 0.20:
                continue

            out.append({
                "source": meta["title"],
                "authority_level": meta["authority_level"],
                "status": meta["status"],
                "applies_to_account": meta["account_id"],
                "text": doc,
                "relevance_score": score,
            })

        # confidence = assess_confidence(out)
        confidence = assess_confidence(out, ctx.account_id)
        return json.dumps({"results": out, "confidence_assessment": confidence}, indent=2)

    @tool
    def query_structured_data(entity_type: str, entity_id: str) -> str:
        """Look up structured ParcelPilot data. entity_type must be one of
        'account', 'order', or 'ticket'. entity_id is the ID, e.g.
        'ACCT-001', 'ORD-1001', 'TKT-501'. Access is automatically scoped
        to what the current user is authorised to see — cross-account
        lookups will return a permission error, report that to the user
        rather than retrying with a different ID."""
        try:
            if entity_type == "account":
                data = db.get_account(ctx, entity_id)
            elif entity_type == "order":
                data = db.get_order(ctx, entity_id)
            elif entity_type == "ticket":
                data = db.get_ticket(ctx, entity_id)
            else:
                return json.dumps({"error": f"Unknown entity_type '{entity_type}'. Use account/order/ticket."})
            data["_snapshot_time_reference"] = db.get_snapshot_time()
            return json.dumps(data, default=str)
        except PermissionError as e:
            return json.dumps({"error": str(e)})

    @tool
    def list_account_tickets(account_id: str) -> str:
        """List all support tickets for a given account_id. Use this when
        you need the full ticket history for an account, e.g. to check for
        related known issues or repeat complaints."""
        try:
            data = db.get_tickets_for_account(ctx, account_id)
            return json.dumps(data, default=str)
        except PermissionError as e:
            return json.dumps({"error": str(e)})

    @tool
    def create_escalation(ticket_id: str, reason: str, proposed_action: str, urgency: str) -> str:
        """Create an escalation to route an issue to a human ParcelPilot
        support/ops staff member. This is a state-changing action — it
        will NOT actually be logged until the user explicitly confirms.
        Call this to PREPARE the escalation; the confirmation step happens
        separately. urgency must be one of 'low', 'medium', 'high', 'critical'.
        reason should state which policy/data made this require human
        judgment (e.g. conflicting sources, exception beyond stated limits,
        no applicable documented policy)."""
        draft = {
            "escalation_id": f"ESC-{uuid.uuid4().hex[:8].upper()}",
            "ticket_id": ticket_id,
            "reason": reason,
            "proposed_action": proposed_action,
            "urgency": urgency,
            "created_by": ctx.user_id,
            "status": "DRAFT_PENDING_CONFIRMATION",
        }
        return json.dumps(draft, indent=2)

    return [search_documents, query_structured_data, list_account_tickets, create_escalation]


def commit_escalation(draft: dict, confirmed_by: str = None) -> dict:
    """Actually 'writes' the escalation — only called after user
    confirmation in the agent/UI layer. Mocked as a local JSON append,
    representing what would be a ticketing-system API call in production.
    Also writes a distinct audit record noting who confirmed it and when —
    separate from the original 'drafted' audit record — so the audit trail
    shows the full lifecycle: drafted -> confirmed -> committed."""
    draft = dict(draft)
    draft["status"] = "CREATED"
    draft["created_at"] = datetime.now().isoformat()
    draft["confirmed_by"] = confirmed_by

    existing = []
    if ESCALATIONS_PATH.exists():
        existing = json.loads(ESCALATIONS_PATH.read_text())
    existing.append(draft)
    ESCALATIONS_PATH.write_text(json.dumps(existing, indent=2))

    try:
        import audit
        audit.log_turn(
            user_id=confirmed_by or "unknown",
            role="ops",
            query=f"[ESCALATION CONFIRMED] {draft.get('escalation_id')}",
            tools_used=["commit_escalation"],
            sources_cited=[],
            confidence_levels=[],
            escalation_drafted=draft,
            escalation_confirmed=True,
            final_answer_preview=f"Escalation {draft.get('escalation_id')} committed by {confirmed_by}.",
        )
    except Exception:
        pass  # audit logging must never block the actual action

    return draft


if __name__ == "__main__":
    ctx = db.UserContext(user_id="ops1", role="ops")
    tools = make_tools(ctx)
    for t in tools:
        print(t.name, "->", t.description[:60])

    search_documents = tools[0]
    print(search_documents.invoke({"query": "P1 response time Northstar"}))
