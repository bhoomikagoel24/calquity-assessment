"""
LangGraph agent for ParcelPilot internal ops support.

Key design points:
- System prompt encodes the source-authority hierarchy explicitly
  (agreement > SOP > policy > product guide > deprecated/tickets).
- create_escalation is interrupted before execution: the graph pauses
  and returns the draft to the caller; nothing is "written" until the
  caller resumes with confirmation (see run_turn / confirm_pending_action).
- Access control lives in tools.py/db.py, not here — the prompt tells
  the model to respect scoping, but the enforcement is structural.
"""

import os
import json
from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, ToolMessage

import db
import tools as tools_module

SYSTEM_PROMPT = """You are ParcelPilot's internal Ops Support Assistant, used by \
authorised ParcelPilot support/operations staff (not customers directly).

Snapshot / "current time" for all time-based reasoning: {snapshot_time}
Use this as "now" for anything involving elapsed time, SLA breaches, or \
pickup-window lateness — do not assume real-world current date.

SOURCE AUTHORITY — apply in this order when sources conflict:
1. Customer-specific signed agreement (highest — overrides everything for that account)
2. Current SOP (Cancellation & Service Credit SOP v4)
3. Current Support Policy v3
4. Product Operations Guide / Known Issues (factual reference, not a policy)
5. Deprecated Support Policy v2 — NEVER use to answer; if retrieved, explicitly \
say you are disregarding it as superseded.
Historical ticket "historical_resolution" fields are CONTEXT ONLY and may be \
WRONG. Never treat a past ticket resolution as authoritative. If it conflicts \
with current documented policy, trust the policy/agreement and note the \
discrepancy rather than repeating the old answer.

WORKING METHOD for any question:
- Identify which account/order/ticket is involved and look it up first if needed.
- Search documents for the applicable clause(s). If the account has a signed \
agreement, check it before falling back to the default SOP/policy.
- Show your reasoning: state which source you used and why it takes precedence, \
especially when a customer agreement overrides a default rule.
- If elapsed-time thresholds matter (e.g. "more than 30 minutes", "more than \
4 hours"), compute them explicitly from the snapshot time and the relevant \
timestamps in the order/ticket record.

WHEN TO ESCALATE (use create_escalation):
- No supplied source answers the question confidently.
- Sources conflict in a way that requires human judgment beyond the stated \
precedence rules.
- The requested exception/credit exceeds a stated limit (e.g. a discretionary \
credit above the SOP's manager-approval threshold, or a monthly cap already reached).
- The issue matches a P1 severity definition (production outage, confirmed or \
suspected security/credential incident) — escalate immediately and say so plainly.
- Do NOT escalate for questions you can answer directly and confidently from \
the supplied sources.

CONFIDENCE: every search_documents call returns a confidence_assessment \
(high/medium/low) plus a reason. Treat this seriously:
- "low" usually means no documented answer exists — say so plainly and \
consider escalating rather than guessing.
- "medium" often means a higher-authority source (e.g. a customer agreement) \
is relevant but wasn't the top text match — always check it before answering, \
don't just use the top-ranked chunk.
- State your confidence level in your answer when it is not high.

CONFIRMATION: create_escalation only prepares a DRAFT. Never claim an escalation \
has actually been created/logged — the system will separately ask the human user \
to confirm before it is committed. Always show the draft and ask for confirmation.

Be concise, cite which document/clause you relied on, and flag your own \
uncertainty rather than guessing.

NUMERIC ACCURACY: any ₹/INR figure you state (a fee, a credit amount, an SLA \
time) must come directly from a retrieved source or a structured-data \
calculation — never estimate or round from memory. If you're not sure a \
number is exactly right, say so explicitly rather than stating it as fact. \
(A deterministic check runs after your answer and will flag any currency \
figure that doesn't appear in your cited sources — so an invented number \
will be caught and shown to the user as unverified.)
"""


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


def build_agent(ctx: db.UserContext):
    tools = tools_module.make_tools(ctx)
    llm = ChatAnthropic(model="claude-sonnet-4-5-20250929", temperature=0)
    llm_with_tools = llm.bind_tools(tools)
    tool_map = {t.name: t for t in tools}
    snapshot_time = db.get_snapshot_time()

    def call_model(state: AgentState):
        messages = state["messages"]
        if not any(isinstance(m, SystemMessage) for m in messages):
            messages = [SystemMessage(content=SYSTEM_PROMPT.format(snapshot_time=snapshot_time))] + messages
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    def call_tools(state: AgentState):
        last = state["messages"][-1]
        results = []
        for call in last.tool_calls:
            tool_fn = tool_map[call["name"]]
            try:
                output = tool_fn.invoke(call["args"])
            except Exception as e:
                # surface the failure to the model as a tool result rather
                # than crashing the whole turn — the agent can then tell
                # the user something went wrong or try a different approach
                output = json.dumps({"error": f"Tool '{call['name']}' failed: {e}"})
            results.append(ToolMessage(content=str(output), tool_call_id=call["id"], name=call["name"]))
        return {"messages": results}

    def should_continue(state: AgentState):
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            # pause before executing an escalation so the caller can confirm
            if any(c["name"] == "create_escalation" for c in last.tool_calls):
                return "await_confirmation"
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", call_tools)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {
        "tools": "tools",
        "await_confirmation": END,  # graph ends; caller inspects last message for the draft
        END: END,
    })
    graph.add_edge("tools", "agent")

    memory = MemorySaver()
    return graph.compile(checkpointer=memory), tool_map


def run_turn(app, thread_id: str, user_text: str, ctx: db.UserContext = None, max_retries: int = 2):
    """Runs one user turn. If the agent produced a create_escalation tool
    call, returns it as a pending action instead of executing it.

    Retries transient API errors (rate limits, connection issues) since a
    support tool failing silently on a blip is worse than a short delay.
    Tool-execution errors (e.g. a malformed lookup) are NOT retried here —
    they're returned to the model as a ToolMessage so it can react
    (e.g. re-ask, or tell the user), consistent with call_tools' behavior.

    If ctx is provided, logs an audit record for this turn (query, tools
    used, sources cited with authority level, confidence seen, and any
    escalation drafted) — see audit.py for why this matters in a
    financial-services support context."""
    from langchain_core.messages import HumanMessage
    import time

    config = {"configurable": {"thread_id": thread_id}}
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            result = app.invoke({"messages": [HumanMessage(content=user_text)]}, config=config)
            break
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(
                f"Agent failed after {max_retries + 1} attempts: {last_error}. "
                f"If this is a rate-limit or connection error, wait and retry; "
                f"otherwise check ANTHROPIC_API_KEY is set correctly."
            ) from last_error

    last = result["messages"][-1]

    pending_escalation = None
    if getattr(last, "tool_calls", None):
        for c in last.tool_calls:
            if c["name"] == "create_escalation":
                pending_escalation = {"tool_call_id": c["id"], "args": c["args"]}

    trace = [
        {"type": type(m).__name__, "content": str(m.content)[:500]}
        for m in result["messages"]
    ]

    if ctx is not None:
        _write_audit_record(ctx, user_text, result["messages"], last, pending_escalation)

    grounding = None
    if isinstance(last.content, str) and last.content.strip() and not getattr(last, "tool_calls", None):
        cited_texts = _collect_cited_source_texts(result["messages"])
        import trust_guard
        grounding = trust_guard.check_numeric_grounding(last.content, cited_texts)

    return last, pending_escalation, trace, grounding


def _collect_cited_source_texts(messages: list) -> list[str]:
    """Pulls the raw text of every document chunk actually returned by
    search_documents this turn, for the numeric grounding check."""
    texts = []
    for m in messages:
        if type(m).__name__ == "ToolMessage" and getattr(m, "name", None) == "search_documents":
            try:
                parsed = json.loads(m.content)
                for r in parsed.get("results", []):
                    texts.append(r.get("text", ""))
            except (json.JSONDecodeError, AttributeError):
                pass
    return texts


def _write_audit_record(ctx: db.UserContext, query: str, messages: list, last_message, pending_escalation):
    import audit
    tools_used = []
    sources_cited = []
    confidence_levels = []
    for m in messages:
        if type(m).__name__ == "ToolMessage":
            tools_used.append(getattr(m, "name", "unknown"))
            if getattr(m, "name", None) == "search_documents":
                try:
                    parsed = json.loads(m.content)
                    for r in parsed.get("results", [])[:3]:  # top matches actually surfaced
                        sources_cited.append(r)
                    conf = parsed.get("confidence_assessment", {}).get("confidence")
                    if conf:
                        confidence_levels.append(conf)
                except (json.JSONDecodeError, AttributeError):
                    pass

    answer_text = last_message.content if isinstance(last_message.content, str) else str(last_message.content)
    audit.log_turn(
        user_id=ctx.user_id,
        role=ctx.role,
        query=query,
        tools_used=tools_used,
        sources_cited=sources_cited,
        confidence_levels=confidence_levels,
        escalation_drafted=pending_escalation["args"] if pending_escalation else None,
        escalation_confirmed=False,  # updated separately when the user actually confirms
        final_answer_preview=answer_text,
    )


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY to test the agent end-to-end.")
    else:
        ctx = db.UserContext(user_id="ops1", role="ops")
        app, tool_map = build_agent(ctx)
        msg, pending, trace, grounding = run_turn(app, "test-thread-1", "Can Northstar cancel ORD-1001 without a cancellation fee? Explain why.", ctx=ctx)
        print(msg.content)
        print("Grounding check:", grounding)
