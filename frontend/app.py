"""
Streamlit UI for the ParcelPilot Internal Ops Assistant.

Two tabs:
  1. Chat — talk to the agent, see which tool it used each step,
     confirm/cancel any proposed escalation before it's committed.
  2. Proactive Issues — ranked ops triage view (Problem 1).

Run with: streamlit run frontend/app.py
Requires ANTHROPIC_API_KEY in the environment.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import streamlit as st
import db
import tools as tools_module
import agent as agent_module
import proactive

st.set_page_config(page_title="ParcelPilot Ops Assistant", layout="wide")

# --- one-time setup: build DB + vector store if not already built ---
if not db.DB_PATH.exists():
    db.build_database()
if not (Path(__file__).parent.parent / "backend" / "chroma_store").exists():
    import ingest
    ingest.build_vector_store()

# --- mock auth: pick a role in the sidebar ---
st.sidebar.title("ParcelPilot Ops Assistant")
st.sidebar.caption("Internal tool — authorised support/ops staff only")
user_id = st.sidebar.text_input("User ID (mock login)", value="ops_agent_1")
role = st.sidebar.selectbox("Role", ["ops"], help="Only 'ops' role is wired up for this internal assistant.")
st.sidebar.divider()
st.sidebar.caption(f"Dataset snapshot time: **{db.get_snapshot_time()}**")

ctx = db.UserContext(user_id=user_id, role=role)

tab_chat, tab_proactive, tab_audit = st.tabs(["💬 Chat", "🚨 Proactive Issues", "📋 Audit Log"])

# ============ CHAT TAB ============
with tab_chat:
    if "app" not in st.session_state:
        st.session_state.app, st.session_state.tool_map = agent_module.build_agent(ctx)
        st.session_state.thread_id = "session-thread"
        st.session_state.history = []  # list of dicts: role, content, trace(optional)
        st.session_state.pending_escalation = None

    st.markdown("Ask about accounts, orders, tickets, cancellations, credits, or SLAs.")
    with st.expander("Example questions"):
        st.markdown("""
        - Can Northstar cancel ORD-1001 without a cancellation fee? Explain why.
        - A pickup is three hours late because of carrier fault on ORD-2002. Should LumenWorks get a service credit?
        - What's going on with TKT-504? Should I tell the customer the pickup failed?
        - TKT-501 looks urgent — what should we do?
        """)

    for turn in st.session_state.history:
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])
            grounding = turn.get("grounding")
            if grounding and not grounding["all_grounded"]:
                st.error(
                    f"⚠️ Unverified figure(s) in this answer: {', '.join('₹' + f for f in grounding['unverified'])} "
                    f"— not found in the text of any cited source. Double-check before relying on this."
                )
            if turn.get("trace"):
                with st.expander("🔧 Tool trace"):
                    for step in turn["trace"]:
                        st.code(f"[{step['type']}] {step['content']}", language=None)

    # pending escalation confirmation UI
    if st.session_state.pending_escalation:
        pe = st.session_state.pending_escalation
        st.warning("⏸️ Agent wants to create an escalation. Review before confirming:")
        st.json(pe["args"])
        col1, col2 = st.columns(2)
        if col1.button("✅ Confirm & Create Escalation"):
            committed = tools_module.commit_escalation(pe["args"], confirmed_by=user_id)
            st.session_state.history.append({
                "role": "assistant",
                "content": f"Escalation **{committed['escalation_id']}** created and logged.\n\n```json\n{json.dumps(committed, indent=2)}\n```",
            })
            st.session_state.pending_escalation = None
            st.rerun()
        if col2.button("❌ Cancel"):
            st.session_state.history.append({
                "role": "assistant",
                "content": "Escalation cancelled — nothing was created.",
            })
            st.session_state.pending_escalation = None
            st.rerun()

    user_input = st.chat_input("Ask a question...", disabled=bool(st.session_state.pending_escalation))
    if user_input:
        st.session_state.history.append({"role": "user", "content": user_input})
        with st.spinner("Thinking..."):
            try:
                last_msg, pending, trace, grounding = agent_module.run_turn(
                    st.session_state.app, st.session_state.thread_id, user_input, ctx=ctx
                )
            except Exception as e:
                st.session_state.history.append({"role": "assistant", "content": f"Error: {e}"})
                st.rerun()

        if pending:
            st.session_state.pending_escalation = pending
            st.session_state.history.append({
                "role": "assistant",
                "content": "I've prepared an escalation draft — please review and confirm below before I create it.",
                "trace": trace,
            })
        else:
            st.session_state.history.append({
                "role": "assistant",
                "content": last_msg.content,
                "trace": trace,
                "grounding": grounding,
            })
        st.rerun()

# ============ PROACTIVE ISSUES TAB ============
with tab_proactive:
    st.markdown("### Ranked ticket triage")
    st.caption("urgency_score = SLA-breach proximity × account tier weight × recurrence factor. "
               "This is a triage aid, not a policy answer — always verify specifics via chat.")
    if st.button("🔄 Refresh"):
        st.rerun()
    results = proactive.compute_urgent_tickets(ctx)
    if not results:
        st.info("No open tickets requiring attention.")
    else:
        st.dataframe(results, use_container_width=True, hide_index=True)

# ============ AUDIT LOG TAB ============
with tab_audit:
    import audit
    st.markdown("### Compliance / Audit Trail")
    st.caption(
        "Every answer is logged with which sources it cited (and their authority level), "
        "the confidence assessment, and the full lifecycle of any escalation "
        "(drafted → confirmed → committed). This is what a financial-institution customer "
        "would ask for during a compliance review or dispute investigation."
    )

    missing = audit.escalations_missing_confirmation()
    if missing:
        st.warning(f"⚠️ {len(missing)} escalation(s) were drafted but never confirmed or cancelled — "
                   "these may need follow-up.")

    records = audit.read_recent(50)
    if not records:
        st.info("No interactions logged yet — ask a question in the Chat tab.")
    else:
        for r in records:
            with st.expander(f"{r['timestamp'][:19]} — {r['user_id']} — \"{r['query'][:70]}\""):
                st.markdown(f"**Tools used:** {', '.join(r['tools_used']) or 'none'}")
                if r["sources_cited"]:
                    st.markdown("**Sources cited:**")
                    for s in r["sources_cited"]:
                        st.markdown(f"- {s['source']} (authority level {s['authority_level']})")
                if r["confidence_levels_seen"]:
                    st.markdown(f"**Confidence seen:** {', '.join(r['confidence_levels_seen'])}")
                if r["escalation_drafted"]:
                    status = "✅ confirmed" if r["escalation_confirmed"] else "⏸️ drafted only"
                    st.markdown(f"**Escalation:** {status}")
                    st.json(r["escalation_drafted"])
                st.markdown(f"**Answer preview:** {r['final_answer_preview']}")
