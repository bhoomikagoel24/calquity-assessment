# ParcelPilot Internal Ops Support Assistant

An internal (staff-facing) AI chatbot for ParcelPilot's support/operations team.
It answers natural-language questions by reasoning over policies, SOPs, customer
agreements, and structured account/order/ticket data — with explicit handling of
source authority, conflicts, and escalation judgment. Also includes a proactive
ticket-triage view.

## Why an internal chatbot (not customer-facing)

Chose the internal ops context because the assessment's hardest and most
interesting requirement — reasoning over imperfect, conflicting, tiered sources —
is best demonstrated there, without spending build time on customer-facing auth
flows that don't add to that core capability. The data/tool-level access control
pattern used here (scoping every DB call to a `UserContext`) would extend
directly to a customer-facing agent later — see the Product Note.

## Setup

```bash
cd backend
python3 -m venv venv && source venv/bin/activate   # optional but recommended
pip install -r requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...

# one-time: build the SQLite DB and the document vector store
python3 db.py
python3 ingest.py
```

## Run

```bash
# from the backend/ directory (or repo root — app.py adjusts sys.path)
streamlit run ../frontend/app.py
```

Open the printed local URL. Sidebar shows the mocked login (role is fixed to
`ops` for this internal tool) and the dataset snapshot time used as "now" for
all time-based reasoning.

## Project layout

```
backend/
  db.py          # SQLite loader + scoped query functions (access control lives here)
  ingest.py       # PDF -> chunked, authority-tagged ChromaDB collection
  tools.py        # the 3 agent tools (doc search, structured lookup, escalation)
  agent.py        # LangGraph agent graph, system prompt, confirmation gating
  proactive.py    # Problem 1: ranked ticket triage scoring
  data/           # the supplied source PDFs + xlsx
frontend/
  app.py          # Streamlit chat UI + proactive issues tab
```

## Testing without the UI

Each backend module has a `__main__` block for standalone testing:
```bash
python3 db.py         # rebuilds DB, prints a sample lookup + access-control check
python3 ingest.py      # rebuilds vector store, prints a sample retrieval + paraphrase test
python3 tools.py       # lists tools, runs a sample document search
python3 proactive.py   # prints the ranked ticket triage list
python3 agent.py       # runs one live agent turn (needs ANTHROPIC_API_KEY)
```

## Automated tests

```bash
cd backend
pytest test_suite.py -v
```
24 tests covering access control, retrieval/authority tagging, confidence
scoring, escalation draft-vs-commit behavior, audit logging, and proactive
triage ranking. No API key required — these test everything deterministic
the agent depends on, not the LLM's own reasoning (verify that live, per
the example queries above).

## Compliance / audit trail

Every chat turn is logged to `backend/audit_log.jsonl` (sources cited,
authority levels, confidence, escalation lifecycle) — viewable in the
Streamlit "Audit Log" tab. See `ARCHITECTURE.md` for why this was added.

## Notes

- Embeddings are TF-IDF (scikit-learn), fit locally on the document corpus —
  no external embedding API or model download required. This was a deliberate
  choice given the corpus size (6 short documents); see the Architecture Note
  for the production swap-out plan (OpenAI/Voyage embeddings).
- `ARCHITECTURE.md` and `PRODUCT_NOTE.md` cover design reasoning, trade-offs,
  and what was intentionally left out.
- `EVAL.md` is a hand-verified golden test set (12 cases) — run each through
  the chat UI and compare against the expected answer before recording the
  demo video. This is the fastest way to catch a prompt-tuning issue.
