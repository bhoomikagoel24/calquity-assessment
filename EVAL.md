# Evaluation Set — Golden Test Cases

Manually verified against the raw source data (not the agent) before writing
this file, so these are ground truth to check the agent's live answers
against — not the agent's own output being copied back as a "test."
Run these through the chat UI after setup and compare.

Snapshot/reference time: **2026-08-16 11:00 Asia/Kolkata**.

---

### 1. "Can Northstar cancel ORD-1001 without a cancellation fee? Explain why."
**Expected: Yes, no fee.**
ORD-1001 is BOOKED, not yet picked up, cancellation requested 2 hours after
booking. SOP default would charge ₹250 (past the 30-minute window), but
Northstar's agreement (authority 1, overrides SOP) waives cancellation fees
on any BOOKED shipment before pickup regardless of elapsed time. Agent
should cite the Northstar agreement, not the SOP, as the deciding source.

### 2. "Customer wants to cancel ORD-1002 — any fee?"
**Expected: Cannot simply cancel — use return-to-origin; no cancellation fee logic applies.**
ORD-1002 status is PICKED_UP (pickup_actual_at 09:35, cancellation
requested 10:20 — after pickup). Northstar's no-fee clause only covers
pre-pickup BOOKED shipments; once PICKED_UP, both the agreement and the SOP
say to use the return-to-origin workflow instead. A correct answer must not
just say "no fee" — it must catch that cancellation isn't the right action
at all post-pickup.

### 3. "Does LumenWorks owe a fee to cancel ORD-2001?"
**Expected: Yes, ₹250 fee.**
Booked 09:00, cancellation requested 10:15 (75 minutes later — past the
30-minute free window). LumenWorks' agreement explicitly does not waive
cancellation fees ("use the current SOP"), so the SOP default (₹250 after
30 minutes) applies.

### 4. "ORD-2002 pickup was missed, carrier's fault — does LumenWorks get a credit, and how much?"
**Expected: Yes, fixed ₹300 credit.**
Pickup window ended 06:30; snapshot time is 11:00 — 4.5 hours past window
end, carrier_fault=True, customer_fault=False. This exceeds LumenWorks'
agreement threshold (>4 hours) which sets a **fixed ₹300** credit,
overriding the SOP's default calculation (lower of ₹500 or 10% of fee =
₹240 here). The agreement number is specifically different from what the
SOP formula alone would produce — a correct answer must use ₹300, not
recompute the SOP formula.

### 5. "Beacon wants to cancel ORD-3001 — fee?"
**Expected: No fee.**
Beacon (ACCT-003) has no custom agreement — SOP defaults apply directly.
Booked 10:25, cancellation requested 10:40 (15 minutes later, within the
30-minute free window).

### 6. "Can we cancel ORD-4001?"
**Expected: No — cannot be cancelled.**
Status is DELIVERED. SOP: delivered orders cannot be cancelled, regardless
of account.

### 7. "TKT-501 just came in — what should happen?"
**Expected: Escalate immediately as P1.**
"All shipment creation is failing" matches the P1 definition (complete
production outage). Northstar's agreement sets P1 response at 15 minutes,
24x7 — tighter than the standard Enterprise 30-minute target. Agent should
flag urgency and prepare an escalation, not attempt to resolve it as a
normal ticket.

### 8. "TKT-502 — customer says bulk upload keeps failing on a 4,200-row file."
**Expected: Answerable directly, no escalation needed.**
Matches known issue KI-208 (uploads fail intermittently above ~3,000 rows
despite the documented 5,000-row limit). Correct answer cites the known
issue and the workaround (split into files under 3,000 rows) — should NOT
repeat the historical ticket TKT-451's claim that "Growth only supports
3,000 rows," which contradicts the current Product Ops Guide.

### 9. "TKT-504 — customer says the order still shows BOOKED 10 minutes after the driver collected it."
**Expected: Don't confirm a problem yet — advise verifying/waiting.**
The referenced order is ORD-1001 (same account, SwiftShip carrier, still
BOOKED). This matches known issue KI-211 (SwiftShip webhook can be up to 20
minutes late). Only 10 minutes have elapsed — within the expected delay
window. Correct answer should NOT tell the customer the pickup failed;
should recommend verifying carrier status or waiting, per the Product Ops
Guide's explicit instruction.

### 10. "TKT-505 — possible API key exposure reported, what do we do?"
**Expected: Escalate immediately as P1 security incident.**
Matches the P1 definition ("suspected credential exposure"). Axis Labs has
no custom agreement, so standard Enterprise P1 target (30 minutes, 24x7)
applies. This is a security incident, not a routine support question —
agent should not attempt to resolve it itself.

### 11. "What did we tell Northstar last time about cancellation fees after 30 minutes?" (tests historical-ticket skepticism)
**Expected: Flag the historical answer as likely wrong, not repeat it.**
TKT-450's historical_resolution says a ₹250 fee was applied — but this
contradicts Northstar's current agreement (no fee, regardless of timing).
Agent should note the discrepancy rather than treating the old ticket as
authoritative, per the system prompt's explicit instruction to treat
`historical_resolution` as context only.

### 12. "A customer on the Standard plan reports a P1-looking issue — what's the response target?"
**Expected: 4 hours (Standard plan, current Policy v3), not 8 hours.**
Tests that the agent uses Policy v3 (current), not the deprecated v2 table
(which says 8 hours for Standard P1), when no customer agreement applies.

---

## How to use this

Run each question through the chat UI (fresh thread per question is
cleanest) and compare the agent's answer + cited source against the
expected result above. A wrong dollar amount, a missed override, or a
repeated historical error is a real regression worth fixing before
recording the demo. Case 2 and case 9 are the best "is this system actually
smart" tests — they require noticing that the obvious interpretation
(just cancel it / confirm the failure) is wrong given the fuller context.
