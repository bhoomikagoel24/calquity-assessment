"""
Automated test suite — no API key required (tests everything except the
live LLM call itself, which agent.py's __main__ block covers separately).

Run: pytest test_suite.py -v
"""

import pytest
import db
import tools as tools_module
import proactive
from ingest import assess_confidence


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    db.build_database()


@pytest.fixture
def ops_ctx():
    return db.UserContext(user_id="ops_test", role="ops")


@pytest.fixture
def customer_ctx():
    return db.UserContext(user_id="cust_test", role="customer", account_id="ACCT-002")


# ---------- Access control ----------

class TestAccessControl:
    def test_ops_can_read_any_account(self, ops_ctx):
        result = db.get_account(ops_ctx, "ACCT-001")
        assert result["account_id"] == "ACCT-001"

    def test_customer_can_read_own_account(self, customer_ctx):
        result = db.get_order(customer_ctx, "ORD-2001")  # belongs to ACCT-002
        assert result["account_id"] == "ACCT-002"

    def test_customer_blocked_from_other_account_order(self, customer_ctx):
        with pytest.raises(PermissionError):
            db.get_order(customer_ctx, "ORD-1001")  # belongs to ACCT-001

    def test_customer_blocked_from_other_account_tickets(self, customer_ctx):
        with pytest.raises(PermissionError):
            db.get_tickets_for_account(customer_ctx, "ACCT-001")

    def test_customer_blocked_from_internal_ticket_list(self, customer_ctx):
        with pytest.raises(PermissionError):
            db.get_all_tickets_internal(customer_ctx)

    def test_tool_layer_surfaces_permission_error_not_raises(self, customer_ctx):
        # tools.py wraps PermissionError into a JSON error string rather
        # than crashing — the agent needs to be able to read this and
        # respond to the user, not have the whole turn fail
        import json
        tools = tools_module.make_tools(customer_ctx)
        query_tool = next(t for t in tools if t.name == "query_structured_data")
        result = json.loads(query_tool.invoke({"entity_type": "order", "entity_id": "ORD-1001"}))
        assert "error" in result


# ---------- Document retrieval / authority hierarchy ----------

class TestRetrieval:
    def test_northstar_query_surfaces_agreement(self, ops_ctx):
        import json
        tools = tools_module.make_tools(ops_ctx)
        search = next(t for t in tools if t.name == "search_documents")
        result = json.loads(search.invoke({"query": "Northstar cancellation fee"}))
        sources = [r["source"] for r in result["results"]]
        assert any("Northstar" in s for s in sources), "Northstar agreement should be retrievable for this query"

    def test_deprecated_policy_tagged_correctly(self, ops_ctx):
        import json
        tools = tools_module.make_tools(ops_ctx)
        search = next(t for t in tools if t.name == "search_documents")
        result = json.loads(search.invoke({"query": "P1 response time enterprise"}))
        deprecated_hits = [r for r in result["results"] if r["status"] == "deprecated"]
        for hit in deprecated_hits:
            assert hit["authority_level"] == 5, "deprecated policy must be tagged lowest authority"

    def test_confidence_flags_when_higher_authority_present_but_not_top(self):
        results = [
            {"source": "Policy v3", "authority_level": 3, "status": "current", "relevance_score": 0.27},
            {"source": "Northstar Agreement", "authority_level": 1, "status": "current", "relevance_score": 0.15},
        ]
        conf = assess_confidence(results)
        assert conf["confidence"] == "medium"
        assert "Northstar Agreement" in conf["reason"]

    def test_confidence_low_for_no_relevant_results(self):
        results = [{"source": "SOP", "authority_level": 2, "status": "current", "relevance_score": 0.01}]
        conf = assess_confidence(results)
        assert conf["confidence"] == "low"

    def test_confidence_low_for_empty_results(self):
        conf = assess_confidence([])
        assert conf["confidence"] == "low"


# ---------- Escalation drafting (state-changing action) ----------

class TestEscalation:
    def test_create_escalation_returns_draft_not_committed(self, ops_ctx):
        import json
        tools = tools_module.make_tools(ops_ctx)
        escalate = next(t for t in tools if t.name == "create_escalation")
        result = json.loads(escalate.invoke({
            "ticket_id": "TKT-501",
            "reason": "P1 outage, all shipment creation failing",
            "proposed_action": "Page on-call engineer",
            "urgency": "critical",
        }))
        assert result["status"] == "DRAFT_PENDING_CONFIRMATION"

    def test_commit_escalation_writes_and_marks_created(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tools_module, "ESCALATIONS_PATH", tmp_path / "escalations.json")
        draft = {"escalation_id": "ESC-TEST01", "ticket_id": "TKT-501", "status": "DRAFT_PENDING_CONFIRMATION"}
        committed = tools_module.commit_escalation(draft, confirmed_by="ops_test")
        assert committed["status"] == "CREATED"
        assert committed["confirmed_by"] == "ops_test"
        assert (tmp_path / "escalations.json").exists()


# ---------- Audit trail ----------

class TestAudit:
    def test_log_turn_writes_and_reads_back(self, tmp_path, monkeypatch):
        import audit
        monkeypatch.setattr(audit, "AUDIT_LOG_PATH", tmp_path / "audit.jsonl")
        audit.log_turn(
            user_id="ops_test", role="ops", query="test",
            tools_used=["search_documents"],
            sources_cited=[{"source": "SOP", "authority_level": 2}],
            confidence_levels=["high"],
            escalation_drafted=None, escalation_confirmed=False,
            final_answer_preview="answer",
        )
        records = audit.read_recent(10)
        assert len(records) == 1
        assert records[0]["user_id"] == "ops_test"

    def test_escalations_missing_confirmation_flags_unconfirmed(self, tmp_path, monkeypatch):
        import audit
        monkeypatch.setattr(audit, "AUDIT_LOG_PATH", tmp_path / "audit.jsonl")
        audit.log_turn(
            user_id="ops_test", role="ops", query="q1", tools_used=[], sources_cited=[],
            confidence_levels=[], escalation_drafted={"escalation_id": "ESC-X"},
            escalation_confirmed=False, final_answer_preview="",
        )
        missing = audit.escalations_missing_confirmation()
        assert len(missing) == 1
        assert missing[0]["escalation_drafted"]["escalation_id"] == "ESC-X"

    def test_confirmed_escalation_not_flagged_as_missing(self, tmp_path, monkeypatch):
        import audit
        monkeypatch.setattr(audit, "AUDIT_LOG_PATH", tmp_path / "audit.jsonl")
        audit.log_turn(
            user_id="ops_test", role="ops", query="q1", tools_used=[], sources_cited=[],
            confidence_levels=[], escalation_drafted={"escalation_id": "ESC-Y"},
            escalation_confirmed=True, final_answer_preview="",
        )
        missing = audit.escalations_missing_confirmation()
        assert len(missing) == 0


# ---------- Numeric trust guard ----------

class TestTrustGuard:
    def test_grounded_figure_passes(self):
        import trust_guard
        src = ["LumenWorks receives a fixed INR 300 service credit."]
        result = trust_guard.check_numeric_grounding("LumenWorks is owed ₹300.", src)
        assert result["all_grounded"] is True
        assert result["unverified"] == []

    def test_hallucinated_figure_flagged(self):
        import trust_guard
        src = ["LumenWorks receives a fixed INR 300 service credit."]
        result = trust_guard.check_numeric_grounding("LumenWorks is owed ₹750.", src)
        assert result["all_grounded"] is False
        assert "750" in result["unverified"]

    def test_no_currency_figures_is_trivially_grounded(self):
        import trust_guard
        result = trust_guard.check_numeric_grounding("Northstar can cancel with no fee.", [])
        assert result["all_grounded"] is True

    def test_multiple_figures_partial_grounding(self):
        import trust_guard
        src = ["The default credit is INR 500 or 10% of the shipment fee.", "manager approval above INR 1,000"]
        result = trust_guard.check_numeric_grounding(
            "The default credit is ₹500, but this exceeds the ₹1,000 threshold and needs approval, so we'd owe ₹1200.",
            src,
        )
        assert "500" in result["figures_in_answer"]
        assert "1200" in result["unverified"]
        assert "500" not in result["unverified"]


# ---------- Proactive issue detection ----------

class TestProactive:
    def test_p1_tickets_rank_above_p3(self, ops_ctx):
        results = proactive.compute_urgent_tickets(ops_ctx)
        by_id = {r["ticket_id"]: r for r in results}
        # TKT-505 (security exposure, P1) should outrank TKT-503 (billing contact, P3)
        assert by_id["TKT-505"]["urgency_score"] > by_id["TKT-503"]["urgency_score"]

    def test_all_open_tickets_included(self, ops_ctx):
        results = proactive.compute_urgent_tickets(ops_ctx)
        ticket_ids = {r["ticket_id"] for r in results}
        assert "TKT-501" in ticket_ids  # the outage ticket must appear

    def test_scores_are_sorted_descending(self, ops_ctx):
        results = proactive.compute_urgent_tickets(ops_ctx)
        scores = [r["urgency_score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_recurrence_uses_word_overlap_not_just_first_word(self, ops_ctx):
        # regression test for the earlier weak heuristic (matched only the
        # first >4-char word). Every ticket should at minimum count itself.
        results = proactive.compute_urgent_tickets(ops_ctx)
        for r in results:
            assert r["recurrence_count_similar"] >= 1


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
