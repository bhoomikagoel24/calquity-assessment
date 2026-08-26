# """
# Streamlit UI for the ParcelPilot Internal Ops Assistant.

# Two tabs:
#   1. Chat — talk to the agent, see which tool it used each step,
#      confirm/cancel any proposed escalation before it's committed.
#   2. Proactive Issues — ranked ops triage view (Problem 1).

# Run with: streamlit run frontend/app.py
# Requires GOOGLE_API_KEY and GROQ_API_KEY in the environment.
# """

# import sys
# import json
# from pathlib import Path

# sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

# import streamlit as st
# import db
# import tools as tools_module
# import agent as agent_module
# import proactive

# st.set_page_config(page_title="ParcelPilot Ops Assistant", layout="wide")

# # --- one-time setup: build DB + vector store if not already built ---
# if not db.DB_PATH.exists():
#     db.build_database()
# if not (Path(__file__).parent.parent / "backend" / "chroma_store").exists():
#     import ingest
#     ingest.build_vector_store()

# # --- mock auth: pick a role in the sidebar ---
# st.sidebar.title("ParcelPilot Ops Assistant")
# st.sidebar.caption("Internal tool — authorised support/ops staff only")
# user_id = st.sidebar.text_input("User ID (mock login)", value="ops_agent_1")
# role = st.sidebar.selectbox("Role", ["ops"], help="Only 'ops' role is wired up for this internal assistant.")
# st.sidebar.divider()
# st.sidebar.caption(f"Dataset snapshot time: **{db.get_snapshot_time()}**")

# ctx = db.UserContext(user_id=user_id, role=role)

# tab_chat, tab_proactive, tab_audit = st.tabs(["💬 Chat", "🚨 Proactive Issues", "📋 Audit Log"])

# # ============ CHAT TAB ============
# with tab_chat:
#     if "app" not in st.session_state:
#         st.session_state.app, st.session_state.tool_map = agent_module.build_agent(ctx)
#         st.session_state.thread_id = "session-thread"
#         st.session_state.history = []  # list of dicts: role, content, trace(optional)
#         st.session_state.pending_escalation = None

#     st.markdown("Ask about accounts, orders, tickets, cancellations, credits, or SLAs.")
#     with st.expander("Example questions"):
#         st.markdown("""
#         - Can Northstar cancel ORD-1001 without a cancellation fee? Explain why.
#         - A pickup is three hours late because of carrier fault on ORD-2002. Should LumenWorks get a service credit?
#         - What's going on with TKT-504? Should I tell the customer the pickup failed?
#         - TKT-501 looks urgent — what should we do?
#         """)

#     for turn in st.session_state.history:
#         with st.chat_message(turn["role"]):
#             st.markdown(turn["content"])
#             grounding = turn.get("grounding")
#             if grounding and not grounding["all_grounded"]:
#                 st.error(
#                     f"⚠️ Unverified figure(s) in this answer: {', '.join('₹' + f for f in grounding['unverified'])} "
#                     f"— not found in the text of any cited source. Double-check before relying on this."
#                 )
#             if turn.get("trace"):
#                 with st.expander("🔧 Tool trace"):
#                     for step in turn["trace"]:
#                         st.code(f"[{step['type']}] {step['content']}", language=None)

#     # pending escalation confirmation UI
#     if st.session_state.pending_escalation:
#         pe = st.session_state.pending_escalation
#         st.warning("⏸️ Agent wants to create an escalation. Review before confirming:")
#         st.json(pe["args"])
#         col1, col2 = st.columns(2)
#         if col1.button("✅ Confirm & Create Escalation"):
#             committed = tools_module.commit_escalation(pe["args"], confirmed_by=user_id)
#             st.session_state.history.append({
#                 "role": "assistant",
#                 "content": f"Escalation **{committed['escalation_id']}** created and logged.\n\n```json\n{json.dumps(committed, indent=2)}\n```",
#             })
#             st.session_state.pending_escalation = None
#             st.rerun()
#         if col2.button("❌ Cancel"):
#             st.session_state.history.append({
#                 "role": "assistant",
#                 "content": "Escalation cancelled — nothing was created.",
#             })
#             st.session_state.pending_escalation = None
#             st.rerun()

#     user_input = st.chat_input("Ask a question...", disabled=bool(st.session_state.pending_escalation))
#     if user_input:
#         st.session_state.history.append({"role": "user", "content": user_input})
#         with st.spinner("Thinking..."):
#             try:
#                 last_msg, pending, trace, grounding = agent_module.run_turn(
#                     st.session_state.app, st.session_state.thread_id, user_input, ctx=ctx
#                 )
#             except Exception as e:
#                 st.session_state.history.append({"role": "assistant", "content": f"Error: {e}"})
#                 st.rerun()

#         if pending:
#             st.session_state.pending_escalation = pending
#             st.session_state.history.append({
#                 "role": "assistant",
#                 "content": "I've prepared an escalation draft — please review and confirm below before I create it.",
#                 "trace": trace,
#             })
#         else:
#             st.session_state.history.append({
#                 "role": "assistant",
#                 "content": last_msg.content,
#                 "trace": trace,
#                 "grounding": grounding,
#             })
#         st.rerun()

# # ============ PROACTIVE ISSUES TAB ============
# with tab_proactive:
#     st.markdown("### Ranked ticket triage")
#     st.caption("urgency_score = SLA-breach proximity × account tier weight × recurrence factor. "
#                "This is a triage aid, not a policy answer — always verify specifics via chat.")
#     if st.button("🔄 Refresh"):
#         st.rerun()
#     results = proactive.compute_urgent_tickets(ctx)
#     if not results:
#         st.info("No open tickets requiring attention.")
#     else:
#         st.dataframe(results, use_container_width=True, hide_index=True)

# # ============ AUDIT LOG TAB ============
# with tab_audit:
#     import audit
#     st.markdown("### Compliance / Audit Trail")
#     st.caption(
#         "Every answer is logged with which sources it cited (and their authority level), "
#         "the confidence assessment, and the full lifecycle of any escalation "
#         "(drafted → confirmed → committed). This is what a financial-institution customer "
#         "would ask for during a compliance review or dispute investigation."
#     )

#     missing = audit.escalations_missing_confirmation()
#     if missing:
#         st.warning(f"⚠️ {len(missing)} escalation(s) were drafted but never confirmed or cancelled — "
#                    "these may need follow-up.")

#     records = audit.read_recent(50)
#     if not records:
#         st.info("No interactions logged yet — ask a question in the Chat tab.")
#     else:
#         for r in records:
#             with st.expander(f"{r['timestamp'][:19]} — {r['user_id']} — \"{r['query'][:70]}\""):
#                 st.markdown(f"**Tools used:** {', '.join(r['tools_used']) or 'none'}")
#                 if r["sources_cited"]:
#                     st.markdown("**Sources cited:**")
#                     for s in r["sources_cited"]:
#                         st.markdown(f"- {s['source']} (authority level {s['authority_level']})")
#                 if r["confidence_levels_seen"]:
#                     st.markdown(f"**Confidence seen:** {', '.join(r['confidence_levels_seen'])}")
#                 if r["escalation_drafted"]:
#                     status = "✅ confirmed" if r["escalation_confirmed"] else "⏸️ drafted only"
#                     st.markdown(f"**Escalation:** {status}")
#                     st.json(r["escalation_drafted"])
#                 st.markdown(f"**Answer preview:** {r['final_answer_preview']}")




"""
Streamlit UI for the ParcelPilot Internal Ops Assistant.

Visual identity: an "ops dispatch panel" rather than a generic chat template.
The core product idea — source authority, not retrieval rank, governs an
answer — is made visible everywhere: the sidebar's Authority Ladder, and
authority/confidence chips on every cited source, in the chat trace, and
in the audit log. Color encodes meaning (authority = shade depth,
confidence = semantic red/amber/green), not decoration.

Three tabs:
  1. Chat — talk to the agent, see which tool it used each step,
     confirm/cancel any proposed escalation before it's committed.
  2. Proactive Issues — ranked ops triage view (Problem 1).
  3. Audit Log — compliance/traceability view.

Run with: streamlit run frontend/app.py
Requires GOOGLE_API_KEY and GROQ_API_KEY in the environment.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
import db
import tools as tools_module
import agent as agent_module
import proactive

from markdown_safety import clean_llm_markdown

try:
    # New, more accurate trust guard (backend/grounding.py). Falls back
    # gracefully if the module hasn't been added to backend/ yet, so this
    # file never hard-crashes an existing checkout.
    from grounding import assess_grounding
    _HAS_GROUNDING_MODULE = True
except ImportError:
    _HAS_GROUNDING_MODULE = False

st.set_page_config(page_title="ParcelPilot Ops Assistant", layout="wide", page_icon="🚚")

# ── Design tokens ────────────────────────────────────────────────────────
# Authority = a shade scale (dark -> light = high -> low authority).
# Confidence = a separate semantic scale (green/amber/red). Keeping these
# two ideas visually distinct (shade vs. hue) mirrors the fact that they
# are genuinely different concepts in the system.
AUTHORITY_COLOR = {
    1: "#0F3D3E",  # customer agreement — darkest, heaviest
    2: "#2C5F7C",  # current SOP
    3: "#54677A",  # current policy
    4: "#8A7A3E",  # product guide (factual, not policy)
    5: "#9AA0A6",  # deprecated — lightest, never authoritative
}
AUTHORITY_LABEL = {
    1: "Agreement",
    2: "SOP",
    3: "Policy",
    4: "Product Guide",
    5: "Deprecated",
}
CONFIDENCE_COLOR = {"high": "#146C43", "medium": "#A66A00", "low": "#B3261E"}
CONFIDENCE_BG = {"high": "#E6F4EA", "medium": "#FCF0DC", "low": "#FBE9E7"}
SEVERITY_COLOR = {"P1": "#B3261E", "P2": "#A66A00", "P3": "#3B4A5A"}
SEVERITY_BG = {"P1": "#FBE9E7", "P2": "#FCF0DC", "P3": "#EAEDF1"}

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');

html, body, [class*="css"] { font-family: 'IBM Plex Sans', sans-serif; }
code, .stCode, .mono { font-family: 'IBM Plex Mono', monospace !important; }

.stApp { background-color: #F4F6F8; }
.block-container { padding-top: 1.6rem; max-width: 1180px; }

/* Sidebar = dispatch panel */
section[data-testid="stSidebar"] {
    background-color: #0F1826;
    color: #E7EBF0;
    border-right: 1px solid #1E2A3D;
}
section[data-testid="stSidebar"] * { color: #E7EBF0 !important; }
section[data-testid="stSidebar"] input, section[data-testid="stSidebar"] select {
    background-color: #1B2636 !important;
    color: #E7EBF0 !important;
    border: 1px solid #33415A !important;
    border-radius: 6px !important;
}
section[data-testid="stSidebar"] .stSelectbox div[data-baseweb="select"] > div {
    background-color: #1B2636 !important;
    border: 1px solid #33415A !important;
    border-radius: 6px !important;
}
section[data-testid="stSidebar"] hr { border-color: #24314A; }

/* Header eyebrow */
.eyebrow {
    font-family: 'IBM Plex Mono', monospace;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    font-size: 0.72rem;
    color: #6B7686;
    margin-bottom: 0.25rem;
    font-weight: 600;
}
.hero-block {
    background: linear-gradient(180deg, #FFFFFF 0%, #F9FAFB 100%);
    border: 1px solid #E4E8ED;
    border-radius: 14px;
    padding: 1.4rem 1.6rem 1.2rem 1.6rem;
    margin-bottom: 1.1rem;
    box-shadow: 0 1px 2px rgba(15, 24, 38, 0.04);
}
.hero-title {
    font-size: 1.85rem;
    font-weight: 700;
    color: #0F1826;
    margin-top: 0;
    margin-bottom: 0.3rem;
    letter-spacing: -0.01em;
}
.hero-sub { color: #4B5566; font-size: 0.95rem; line-height: 1.5; margin-bottom: 0; max-width: 62ch; }

/* Snapshot chip */
.snapshot-chip {
    display: inline-block;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.76rem;
    background: #0F1826;
    color: #7FD8C6 !important;
    padding: 3px 10px;
    border-radius: 20px;
    border: 1px solid #2A3A52;
}
.sidebar-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.68rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #7A8496 !important;
    margin-bottom: 0.2rem;
}

/* Authority ladder (signature sidebar element) */
.ladder-title {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.72rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #8B95A5;
    margin: 0.2rem 0 0.6rem 0;
    border-top: 1px solid #24314A;
    padding-top: 1rem;
}
.ladder-rung {
    display: flex;
    align-items: center;
    gap: 9px;
    margin-bottom: 6px;
}
.ladder-bar {
    height: 13px;
    border-radius: 3px;
    flex-shrink: 0;
}
.ladder-label {
    font-size: 0.78rem;
    color: #C4CCD8 !important;
}
.ladder-deprecated {
    background-image: repeating-linear-gradient(45deg, #9AA0A6, #9AA0A6 3px, #7A7F85 3px, #7A7F85 6px);
}

/* Chips used throughout: authority + confidence + severity */
.chip {
    display: inline-flex;
    align-items: center;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.68rem;
    font-weight: 600;
    color: white;
    padding: 3px 9px;
    border-radius: 20px;
    margin-right: 5px;
    white-space: nowrap;
    letter-spacing: 0.02em;
}
.chip-soft {
    display: inline-flex;
    align-items: center;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.72rem;
    font-weight: 600;
    padding: 3px 10px;
    border-radius: 20px;
    margin-right: 6px;
    letter-spacing: 0.02em;
}

/* Tabs as a segmented control instead of default underline tabs */
.stTabs [data-baseweb="tab-list"] {
    gap: 4px;
    background-color: #E4E8ED;
    padding: 4px;
    border-radius: 10px;
    margin-bottom: 0.6rem;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 7px;
    background-color: transparent;
    color: #4B5566;
    font-weight: 600;
    font-size: 0.92rem;
}
.stTabs [aria-selected="true"] {
    background-color: #0F1826 !important;
    color: white !important;
}

/* Chat bubbles */
div[data-testid="stChatMessage"] {
    border-radius: 12px;
    padding: 10px 4px;
}
div[data-testid="stChatMessageContent"] p { line-height: 1.55; }

/* Answer section cards */
.answer-card {
    background: #FFFFFF;
    border: 1px solid #E4E8ED;
    border-radius: 10px;
    padding: 0.2rem 1rem 0.7rem 1rem;
    margin-bottom: 0.5rem;
}
.section-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.68rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #8B95A5;
    font-weight: 600;
    margin: 0.7rem 0 0.35rem 0;
}
.trust-ok {
    display: flex;
    align-items: center;
    gap: 8px;
    background: #E6F4EA;
    border: 1px solid #B7E0C3;
    color: #146C43;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 0.85rem;
    margin: 0.4rem 0;
}
.trust-warn {
    background: #FBE9E7;
    border: 1px solid #F0B8B0;
    color: #7A160C;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 0.88rem;
    margin: 0.4rem 0;
}
.source-row {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.8rem;
    padding: 6px 0;
    border-bottom: 1px solid #EBEDF0;
    display: flex;
    align-items: center;
    gap: 6px;
}
.source-row:last-child { border-bottom: none; }

/* Escalation confirmation card */
.escalation-card {
    border: 1.5px solid #A66A00;
    background: #FFF8EC;
    border-radius: 10px;
    padding: 0.9rem 1.1rem;
    margin-bottom: 0.7rem;
}

/* Proactive table */
.triage-table { width: 100%; border-collapse: collapse; font-size: 0.87rem; }
.triage-table th {
    text-align: left; padding: 8px 10px; border-bottom: 2px solid #0F1826;
    color: #4B5566; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em;
    font-family: 'IBM Plex Mono', monospace;
}
.triage-table td { padding: 9px 10px; border-bottom: 1px solid #EBEDF0; vertical-align: middle; }
.triage-table tr:hover { background: #F9FAFB; }
.triage-num { font-family: 'IBM Plex Mono', monospace; text-align: right; display: block; }
.triage-subject {
    max-width: 340px; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; display: block;
}
.audit-preview { color: #4B5566; font-size: 0.88rem; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def authority_chip(level: int) -> str:
    color = AUTHORITY_COLOR.get(level, "#6B7686")
    label = AUTHORITY_LABEL.get(level, f"Level {level}")
    return f'<span class="chip" style="background:{color}">{label}</span>'


def confidence_chip(level: str) -> str:
    color = CONFIDENCE_COLOR.get(level, "#6B7686")
    bg = CONFIDENCE_BG.get(level, "#EAEDF1")
    return f'<span class="chip-soft" style="background:{bg};color:{color}">● {level.upper()} CONFIDENCE</span>'


def severity_chip(sev: str) -> str:
    color = SEVERITY_COLOR.get(sev, "#6B7686")
    bg = SEVERITY_BG.get(sev, "#EAEDF1")
    return f'<span class="chip-soft" style="background:{bg};color:{color}">{sev}</span>'


def render_authority_ladder():
    st.sidebar.markdown('<div class="ladder-title">Source Authority</div>', unsafe_allow_html=True)
    widths = {1: 100, 2: 84, 3: 68, 4: 52, 5: 36}
    for level in range(1, 6):
        label = AUTHORITY_LABEL[level]
        color = AUTHORITY_COLOR[level]
        bar_class = "ladder-bar ladder-deprecated" if level == 5 else "ladder-bar"
        bar_style = "" if level == 5 else f"background:{color};"
        st.sidebar.markdown(
            f'<div class="ladder-rung">'
            f'<div class="{bar_class}" style="width:{widths[level]}px;{bar_style}"></div>'
            f'<div class="ladder-label">{level}. {label}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    st.sidebar.caption("Higher rung overrides lower when both apply to an account.")


def compute_grounding(answer_text, trace: list) -> dict:
    """Compute grounding safely even when LangChain returns structured content."""
    if not _HAS_GROUNDING_MODULE:
        return {
            "all_grounded": True,
            "unverified": [],
            "document_grounded": [],
            "structured_data_grounded": [],
            "derived_from_grounded_evidence": [],
            "figures_checked": [],
        }

    # Gemini/LangChain can sometimes return message.content as a list
    # instead of a plain string. Grounding expects text.
    if isinstance(answer_text, list):
        parts = []

        for item in answer_text:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)

        answer_text = "\n".join(parts)

    elif not isinstance(answer_text, str):
        answer_text = str(answer_text)

    return assess_grounding(answer_text, trace)


def render_trust_banner(grounding: dict):
    """Only ever shows something when it's actually informative."""
    if not grounding:
        return
    unverified = grounding.get("unverified") or []
    derived = grounding.get("derived_from_grounded_evidence") or []
    if unverified:
        items = ", ".join(f"INR {f}" for f in unverified)
        st.markdown(
            f'<div class="trust-warn">⚠️ <strong>Verification needed</strong><br>'
            f'{items} could not be traced to any cited source or structured record for this answer. '
            f'Double-check before relying on this figure.</div>',
            unsafe_allow_html=True,
        )
    elif derived:
        st.markdown(
            '<div class="trust-ok">✓ Every figure in this answer is grounded in retrieved '
            'evidence — including calculated amounts derived from grounded inputs.</div>',
            unsafe_allow_html=True,
        )


def extract_sources_and_confidence(trace: list):
    sources_seen, confidences_seen = [], []
    for step in trace or []:
        if step["type"] == "ToolMessage":
            try:
                parsed = json.loads(step["content"])
            except (json.JSONDecodeError, TypeError):
                continue
            for r in parsed.get("results", [])[:3]:
                sources_seen.append(r)
            conf = parsed.get("confidence_assessment", {})
            if conf.get("confidence"):
                confidences_seen.append(conf)
    return sources_seen, confidences_seen


def render_answer(turn: dict):
    """Render one assistant turn with a clear visual hierarchy:
    decision/answer -> trust banner -> sources & confidence -> raw trace."""

    content = turn.get("content", "")

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        content = "\n".join(parts)

    elif not isinstance(content, str):
        content = str(content)

    cleaned = clean_llm_markdown(content)
    st.markdown(cleaned)

    grounding = turn.get("grounding")
    if grounding:
        render_trust_banner(grounding)

    trace = turn.get("trace")
    if not trace:
        return

    sources_seen, confidences_seen = extract_sources_and_confidence(trace)

    if sources_seen or confidences_seen:
        st.markdown('<div class="section-label">Sources & Confidence</div>', unsafe_allow_html=True)
        if confidences_seen:
            conf = confidences_seen[-1]
            st.markdown(confidence_chip(conf["confidence"]), unsafe_allow_html=True)
            if conf.get("reason"):
                st.caption(conf["reason"])
        rows = "".join(
            f'<div class="source-row">{authority_chip(s["authority_level"])} '
            f'<span>{s["source"]}</span> '
            f'<span style="color:#8B95A5;margin-left:auto;">relevance {s.get("relevance_score", "?")}</span></div>'
            for s in sources_seen
        )
        st.markdown(f'<div class="answer-card" style="padding-top:0.5rem;">{rows}</div>', unsafe_allow_html=True)

    with st.expander("🔧 Raw tool trace"):
        for step in trace:
            st.code(f"[{step['type']}] {step['content']}", language=None)


# --- one-time setup: build DB + vector store if not already built ---
if not db.DB_PATH.exists():
    db.build_database()
if not (Path(__file__).parent.parent / "backend" / "chroma_store").exists():
    import ingest
    ingest.build_vector_store()

# --- sidebar: mock auth + signature Authority Ladder ---
st.sidebar.markdown('<div class="eyebrow">Internal Tool</div>', unsafe_allow_html=True)
st.sidebar.markdown("### ParcelPilot Ops")
st.sidebar.caption("Authorised support/ops staff only")
st.sidebar.markdown('<div class="sidebar-label">User ID (mock login)</div>', unsafe_allow_html=True)
user_id = st.sidebar.text_input("User ID", value="ops_agent_1", label_visibility="collapsed")
st.sidebar.markdown('<div class="sidebar-label">Role</div>', unsafe_allow_html=True)
role = st.sidebar.selectbox(
    "Role", ["ops"], help="Only 'ops' role is wired up for this internal assistant.",
    label_visibility="collapsed",
)
st.sidebar.markdown(
    f'<div style="margin-top:10px;">'
    f'<div class="sidebar-label">Dataset snapshot</div>'
    f'<span class="snapshot-chip">{db.get_snapshot_time()}</span></div>',
    unsafe_allow_html=True,
)
render_authority_ladder()

ctx = db.UserContext(user_id=user_id, role=role)

# --- header ---
st.markdown(
    '<div class="hero-block">'
    '<div class="eyebrow">Evidence-grounded operational support</div>'
    '<div class="hero-title">🚚 ParcelPilot Ops Assistant</div>'
    '<div class="hero-sub">Source-aware reasoning over policies, agreements, and structured account data — '
    'escalations require human confirmation, every answer is logged.</div>'
    '</div>',
    unsafe_allow_html=True,
)

tab_chat, tab_proactive, tab_audit = st.tabs(["💬 Chat", "🚨 Proactive Issues", "📋 Audit Log"])

# ============ CHAT TAB ============
with tab_chat:
    if "app" not in st.session_state:
        st.session_state.app, st.session_state.tool_map = agent_module.build_agent(ctx)
        st.session_state.thread_id = "session-thread"
        st.session_state.history = []  # list of dicts: role, content, trace(optional)
        st.session_state.pending_escalation = None

    st.caption("Ask about accounts, orders, tickets, cancellations, credits, or SLAs.")
    with st.expander("Example questions"):
        st.markdown("""
- Can Northstar cancel ORD-1001 without a cancellation fee? Explain why.
- A pickup is three hours late because of carrier fault on ORD-2002. Should LumenWorks get a service credit?
- What's going on with TKT-504? Should I tell the customer the pickup failed?
- TKT-501 looks urgent — what should we do?
""")

    for turn in st.session_state.history:
        with st.chat_message(turn["role"]):
            if turn["role"] == "assistant":
                render_answer(turn)
            else:
                st.markdown(turn["content"])

    # pending escalation confirmation UI
    if st.session_state.pending_escalation:
        pe = st.session_state.pending_escalation
        st.markdown(
            '<div class="escalation-card">⏸️ <strong>Agent wants to create an escalation.</strong> '
            'Review the details below before confirming.</div>',
            unsafe_allow_html=True,
        )
        st.json(pe["args"])
        col1, col2 = st.columns(2)
        if col1.button("✅ Confirm & Create Escalation", use_container_width=True):
            committed = tools_module.commit_escalation(pe["args"], confirmed_by=user_id)
            st.session_state.history.append({
                "role": "assistant",
                "content": f"Escalation **{committed.get('escalation_id', 'UNKNOWN')}** created and logged.\n\n```json\n{json.dumps(committed, indent=2)}\n```",
                # "content": f"Escalation **{committed['escalation_id']}** created and logged.\n\n```json\n{json.dumps(committed, indent=2)}\n```",
            })
            st.session_state.pending_escalation = None
            st.rerun()
        if col2.button("❌ Cancel", use_container_width=True):
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
            # Recompute grounding with the improved trust guard so existing
            # agent.py implementations benefit immediately, even before
            # they're updated to call grounding.assess_grounding() directly.
            recomputed_grounding = compute_grounding(last_msg.content, trace) if _HAS_GROUNDING_MODULE else grounding
            st.session_state.history.append({
                "role": "assistant",
                "content": last_msg.content,
                "trace": trace,
                "grounding": recomputed_grounding,
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
        rows_html = ""
        for r in results:
            chip = severity_chip(r["estimated_severity"])
            rows_html += (
                f'<tr>'
                f'<td style="font-family:\'IBM Plex Mono\';font-size:0.82rem;">{r["ticket_id"]}</td>'
                f'<td>{chip}</td>'
                f'<td>{r["account_name"]} <span style="color:#8B95A5;">({r["plan"]})</span></td>'
                f'<td><span class="triage-subject" title="{r["subject"]}">{r["subject"]}</span></td>'
                f'<td><span class="triage-num">{r["hours_open"]}h</span></td>'
                f'<td><span class="triage-num">{r["recurrence_count_similar"]}</span></td>'
                f'<td><span class="triage-num" style="font-weight:600;">{r["urgency_score"]}</span></td>'
                f'</tr>'
            )
        st.markdown(
            f"""
            <table class="triage-table">
            <thead>
            <tr><th>Ticket</th><th>Severity</th><th>Account</th><th>Subject</th>
            <th style="text-align:right;">Hours Open</th><th style="text-align:right;">Related</th>
            <th style="text-align:right;">Urgency</th></tr>
            </thead>
            <tbody>{rows_html}</tbody>
            </table>
            """,
            unsafe_allow_html=True,
        )

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
                st.markdown(f"**Tools used:** `{', '.join(r['tools_used']) or 'none'}`")
                if r["sources_cited"]:
                    st.markdown("**Sources cited:**")
                    chips = "".join(
                        f'<div class="source-row">{authority_chip(s["authority_level"])} {s["source"]}</div>'
                        for s in r["sources_cited"]
                    )
                    st.markdown(chips, unsafe_allow_html=True)
                if r["confidence_levels_seen"]:
                    chips = " ".join(confidence_chip(c) for c in r["confidence_levels_seen"])
                    st.markdown(f"**Confidence seen:** {chips}", unsafe_allow_html=True)
                if r["escalation_drafted"]:
                    status = "✅ confirmed" if r["escalation_confirmed"] else "⏸️ drafted only"
                    st.markdown(f"**Escalation:** {status}")
                    st.json(r["escalation_drafted"])
                st.markdown('<div class="section-label">Answer preview</div>', unsafe_allow_html=True)
                st.markdown(
                    f'<div class="audit-preview">{clean_llm_markdown(r["final_answer_preview"])}</div>',
                    unsafe_allow_html=True,
                )
                full_answer = r.get("final_answer_full") or r.get("full_answer")
                if full_answer and full_answer != r["final_answer_preview"]:
                    with st.expander("Full answer"):
                        st.markdown(clean_llm_markdown(full_answer))