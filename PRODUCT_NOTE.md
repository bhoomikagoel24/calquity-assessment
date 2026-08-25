# Product Note

## Which additional problem I chose

**Problem 1: Proactive Issue Detection.** The trust/reliability problem is
already addressed structurally throughout the core chatbot (authority
hierarchy, explicit conflict handling, skepticism toward historical ticket
guidance — see Architecture Note), so proactive detection was the higher-
leverage addition: it's a genuinely new capability rather than more of the
same reasoning pattern.

**Approach**: `proactive.py` computes an explainable `urgency_score` per open
ticket = `SLA-breach ratio × account-tier weight × recurrence factor`, where:
- breach ratio compares hours-open against a rough severity-based SLA target
- tier weight favors Enterprise/premium accounts
- recurrence factor bumps tickets that share subject keywords with other
  open tickets (a crude proxy for "multiple customers hitting the same issue")

Deliberately kept the scoring simple and inspectable rather than a
black-box model — for a triage tool, "why is this ranked #1" needs a
one-sentence answer an ops lead can sanity-check, especially early in
adoption when trust is still being built.

Tested against the data pack: the two P1-severity tickets (Northstar's
total outage, Axis Labs' possible credential exposure) correctly surface
at the top; a routine billing-contact-change request correctly sinks to
the bottom.

## What else I'd build next (priority order)

0. **(Already added, not hypothetical)** An audit trail (`audit.py` +
   Audit Log tab) logging every answer's sources, confidence, and escalation
   lifecycle — for a financial-institution customer base, this is closer to
   a requirement than a nice-to-have, so I built it directly rather than
   just listing it here.

1. **Customer-facing chatbot on the same backend.** The `UserContext` /
   scoped-query pattern in `db.py` was built to extend directly to a
   `role="customer"` context — the harder remaining work is a real auth
   layer (today it's a mocked context object) and rate-limiting/abuse
   controls before exposing this externally.
2. **Confidence surfaced in the UI itself**, not just in the model's prose —
   a visible badge (High/Medium/Low, or "escalation recommended") per answer,
   driven by whether sources conflicted or were fully absent, so an ops
   agent can scan quickly instead of reading full reasoning every time.
3. **Real embeddings + reranking** once the document set grows beyond a
   handful of files — TF-IDF won't scale semantically to paraphrased
   customer questions against a much larger policy corpus.
4. **A feedback loop on historical tickets** — flag ticket resolutions that
   contradict current policy (like TKT-450/451 in this data) for someone to
   actually correct or archive, instead of relying on the agent to catch it
   at answer-time forever.
5. **Structured escalation review queue** — right now escalations write to
   a flat JSON file; a real version needs a queue a human can triage,
   reassign, and close, feeding back into the proactive-issues view.

## What I intentionally left out

- Customer-facing auth/session management (see #1 above)
- Real embedding API integration (documented trade-off, not an oversight)
- Streaming responses
- Automated tests / CI — given the time budget, verification was done via
  each module's `__main__` block and manual testing against the example
  queries plus several I constructed from the data pack
- Rate limiting, logging/observability, and prompt-injection hardening on
  document content — worth doing before any real deployment, out of scope
  for a first-round assessment

## One metric I'd use to judge usefulness

**Percentage of ops queries resolved without a human needing to re-check the
source documents themselves.** This single number captures both halves of
the requirement at once: if it's low because the agent escalates everything,
it's not saving time; if it's high but built on wrong or overconfident
answers, that shows up as ops staff catching errors on re-check and the
number won't hold up under a spot-check audit. Tracking it also naturally
surfaces which document types or conflict patterns the agent struggles with,
which is where to invest next.
