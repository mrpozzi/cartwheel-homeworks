# Review summary (Homework 4)

Reviewed on 2026-09-24 and 2026-09-25 with the interface under `analysis/review_app/`.
Traces come from the Homework 3 run (prompt version `f45891500f2f`, model
`gpt-5.5-2026-04-23`): 466 Langfuse traces in 250 sessions, one trace per user
turn. The unit of review and of every label is the trace; the interface always
shows the whole session around it.

## Reviewed sample

121 distinct traces from 89 sessions, none counted in more than one batch.
All 121 carry a verdict (60 with at least one failure note, 61 marked "no failure observed").

| Batch | Method | Traces | With a failure | No failure |
| --- | --- | --- | --- | --- |
| B0-opportunistic | opportunistic_review | 8 | 8 | 0 |
| B1-uniform | uniform | 15 | 3 | 12 |
| B1-cluster | cluster_representatives | 15 | 6 | 9 |
| B2-intent | dimension | 30 | 8 | 22 |
| B3-depth | depth_search | 26 | 21 | 5 |
| B4-final | uniform | 15 | 2 | 13 |
| B3-depth-scan | depth_search_model_assisted | 12 | 12 | 0 |

The handout's four batches are B1 (uniform plus cluster representatives), B2
(spread across the nine intents, with difficulty as a secondary key inside each
intent), B3 (depth searches: structural searches for four modes plus a
model-assisted scan for one), and B4 (the final uniform batch). B0 holds the
8 traces annotated while reading a neighbouring sampled turn; they are
positives the taxonomy relies on and had to be in the labeled set. Selection
details, seeds, and one reason per trace are in
`analysis/state/sample_manifest.json`.

Composition of the 121 traces: roles shopper 76, merchant 26, support 19; intents refund 28, product_search 22, order_status 20, return_eligibility 15, out_of_scope 9, cancellation 8, account_change 8, policy_question 6, dispute 5;
difficulty well_specified 99, correction_across_turns 7, missing_information 6, ambiguous 5, boundary 4; position later turn 54, single-turn session 35, first turn 32. The composition is deliberately
non-uniform (depth searches and the intent spread), so the counts below are
sample fractions, not prevalence estimates. Homework 5 estimates prevalence on
the full trace store.

## Final taxonomy: seven modes

| Mode | Present | Sample fraction | Evaluator | Requirement |
| --- | --- | --- | --- | --- |
| `unneeded_escalation` | 8 | 0.066 | llm judge | ESC-1 |
| `duplicate_escalation` | 10 | 0.083 | code check | ESC-1 (revised) |
| `out_of_scope_request_fulfilled` | 5 | 0.041 | llm judge | SCOPE-2, RESP-5 |
| `record_inconsistency_not_surfaced` | 7 | 0.058 | llm judge | RESP-3, ESC-3 |
| `policy_claim_without_citation` | 12 | 0.099 | code check | RESP-1 |
| `raw_record_fields_in_reply` | 4 | 0.033 | code check | RESP-6 (new) |
| `unsupported_assertion` | 19 | 0.157 | llm judge | RESP-3 |

Every trace received one present-or-absent judgment per mode (121 × 7 = 847
judgments), each written to Langfuse as a numeric score named after the mode
and mirrored append-only under `analysis/state/labels/`. 5 traces are
present for two modes: support-0085 t2 (unneeded_escalation, duplicate_escalation); support-0089 t1 (unneeded_escalation, out_of_scope_request_fulfilled); support-0042 t3 (policy_claim_without_citation, unsupported_assertion); support-0057 t2 (policy_claim_without_citation, unsupported_assertion); support-0166 t4 (policy_claim_without_citation, unsupported_assertion).
Two behaviours observed during review were recorded as a tool defect and a
tool gap rather than modes (see the specification revisions), and one
comment-only observation (an unrelated product surfacing in a search) stayed
outside the taxonomy.

### `unneeded_escalation`

- **Definition.** The agent calls escalate_to_human in a turn where no human involvement is required at all: the request was resolved from policy and the order record (an informational question, a past-window denial the policy settles, a request the agent simply cannot fulfil such as sending an email).
- **Boundary.** Not this mode when the request is an account or payment change (ESC-2), a dispute, or a record inconsistency the agent cannot resolve (ESC-3). A ticket opened for an issue already under human review is duplicate_escalation, not this mode.
- **Requirement source.** ESC-1..ESC-4 read strictly, TOOL-9 side effect; proposed clarification: escalate only in the ESC cases
- **Likely evaluator.** llm judge. **Product change.** Tighten the escalation criteria in the system prompt; consider a code check for a ticket opened after issue_refund returned queued_for_approval or after an earlier ticket in the session.
- **Confirmed positives (6).** support-0243 t2, support-0025 t1, support-0161 t1, support-0045 t1, support-0116 t1, support-0213 t1
- **Close negatives (6).** support-0231 t1, support-0228 t1, support-0052 t1, support-0021 t1, support-0135 t1, support-0250 t1
- **Origin annotations.** `mufqbuwfeu8ze`, `mufr2p8o53vsb`, `mufux0oo2uqjo`, `mufwma7gnib8i`, `mugh8cnwi3l95`, `mughiz9f8r0cc`

### `duplicate_escalation`

- **Definition.** The agent opens a support ticket for an issue that is already under human review in the same session: issue_refund returned queued_for_approval for it, or an earlier turn already opened a ticket for it.
- **Boundary.** The first ticket for an issue is not this mode even if the issue did not need one (that is unneeded_escalation). A ticket for a genuinely new issue in the same session is not this mode. Escalating a queued refund because the user reports a new problem with it is a judgment call to record.
- **Requirement source.** ESC-1 revision (decided 2026-09-25): above the threshold the queue is the human review; the agent explains the result and does not open a separate ticket. Motivating annotations: mufsrxnlq9lv7 (support-0085 t2), mufvlhb52bfvr (support-0228 t3). Note: the system prompt's Escalation section currently instructs the ticket, so the fix is a prompt change.
- **Likely evaluator.** code check. **Product change.** Prompt: do not call escalate_to_human when issue_refund already queued the refund or a ticket for the same issue exists; code check: escalate_to_human after queued_for_approval in the same turn, or a second ticket in a session.
- **Confirmed positives (8).** support-0085 t2, support-0228 t3, support-0007 t1, support-0004 t1, support-0195 t1, support-0178 t1, support-0161 t2, support-0231 t3
- **Close negatives (5).** support-0231 t1, support-0085 t1, support-0110 t1, support-0149 t1, support-0238 t1
- **Origin annotations.** `mufsrxnlq9lv7`, `mufvlhb52bfvr`, `mufxi53d5sg94`, `mufxhvltmmv85`, `mughhm98245kq`, `mugih5i39yyue`, `mugiiow7qnv26`, `mugij80x8w33l`

### `out_of_scope_request_fulfilled`

- **Definition.** The agent answers, attempts, or engages with a request that SCOPE-2 says to refuse (legal advice, payment credentials, anything outside Cartwheel) instead of declining in one or two sentences and pointing to what it can help with. Engaging includes giving steps for another marketplace's process or opening a ticket and offering to organise a comparison after nominally declining.
- **Boundary.** Declining and escalating a payment-card change is not this mode. A brief refusal that points to Cartwheel help is not this mode. The ticket opened on support-0089 is also an unneeded escalation; the same trace can carry both.
- **Requirement source.** SCOPE-2, RESP-5
- **Likely evaluator.** llm judge. **Product change.** Scope enforcement in the system prompt with refusal examples for general-knowledge requests about purchased items.
- **Confirmed positives (5).** support-0139 t1, support-0139 t2, support-0139 t3, support-0026 t1, support-0089 t1
- **Close negatives (3).** support-0228 t1, support-0228 t2, support-0035 t1
- **Origin annotations.** `mufuio948iu3a`, `mufuiyyzmsghv`, `mufuj82z9el5o`, `muggtywc6gkty`, `mugh01rhbdels`

### `record_inconsistency_not_surfaced`

- **Definition.** The order record needed for the answer is missing or inconsistent (delivered with no delivery date, dates out of order), and the agent either fails to state the inconsistency that bears on the question or works around it by asking the user for the value or computing from hypothetical values, instead of stating it and escalating.
- **Boundary.** Stating the inconsistency and escalating is not this mode even when the reply is long. A hedged estimate offered after the gap is stated and the case is escalated (Workshop replay W8, workshop-0006) is not this mode; the mode is computing from hypothetical values or asking the user for the missing value instead of escalating. Asking the user for information the record legitimately lacks (which order they mean) is not this mode.
- **Requirement source.** RESP-3, ESC-3
- **Likely evaluator.** llm judge. **Product change.** Data-quality rule in the prompt: never compute eligibility from user-supplied dates; state every inconsistency and escalate.
- **Confirmed positives (7).** support-0132 t1, support-0021 t2, support-0052 t1, support-0105 t1, support-0164 t1, support-0090 t1, support-0019 t1
- **Close negatives (4).** support-0021 t1, support-0031 t1, support-0124 t1, support-0103 t2
- **Origin annotations.** `mufud2ozctjwe`, `mufsve3w5o0iq`, `mufrkzqoplv1l`, `mugh1hzonebm3`, `mughgp6gs3nec`, `mugh0wcya5ixd`, `muggqssjvoicx`, `muggrl4v2e0gq`

### `policy_claim_without_citation`

- **Definition.** The reply states a rule derived from a policy document (a window length, a threshold, a fee, a timeline, a follow-up promise) without citing the policy identifier for that rule, whether the policy was never retrieved in the session (retrieval failure) or was retrieved but not cited.
- **Boundary.** A rule cited with the right identifier in the same reply is not this mode. Values that exist only in the order record (a status, a total, a date) need no citation. Reviewer decision 2026-09-25 (support-0017): a number that also appears in a policy document, such as the 24-hour follow-up or the 5-10 day refund timing, needs the policy identifier even when a tool result carried it.
- **Requirement source.** RESP-1
- **Likely evaluator.** code check. **Product change.** Citation instruction and retrieval of cw-returns before stating the platform default.
- **Confirmed positives (8).** support-0201 t1, support-0008 t1, support-0070 t1, support-0200 t1, support-0038 t2, support-0094 t2, support-0038 t3, support-0017 t1
- **Close negatives (3).** support-0104 t1, support-0158 t1, support-0246 t1
- **Origin annotations.** `mufvih5rbwlkt`, `mufxk9sojebtu`, `muggz9upe693d`, `mughi9dfofoip`, `mugik7uvxo15o`, `mugijm5zdbd3c`, `mugik6brj5thu`, `mufxjqgirwsxs`, `mugi7lpb0aio0`

### `raw_record_fields_in_reply`

- **Definition.** A user-facing reply contains a raw record field name or identifier from a tool result (for example refund_eligible: false, store_id, product_id, delivered_at) instead of plain language.
- **Boundary.** Plain-language statements of the same fact ("not eligible for a refund", "sold by Petal & Stem") are not this mode. Order numbers and ticket numbers are user-facing identifiers and are not this mode. Extra but relevant detail is not this mode; the former reply_noise clause about irrelevant content was dropped as a taste judgment.
- **Requirement source.** proposed RESP-6: replies use plain language and never expose raw record field names or internal identifiers
- **Likely evaluator.** code check. **Product change.** Reply-style instruction in the system prompt; a regex over replies for known field names as the check.
- **Confirmed positives (4).** support-0001 t1, support-0002 t1, support-0184 t1, support-0079 t2
- **Close negatives (3).** support-0104 t1, support-0185 t1, support-0103 t2
- **Origin annotations.** `mufr08q4ghlpg`, `mufxhjvti3qwg`, `mughh4o8g9cr1`, `mugilbnhq3mlo`

### `unsupported_assertion`

- **Definition.** The reply or a tool argument states a specific value or promise (amount, limit, timeline, eligibility) that no tool result or retrieved policy in the session supports, without flagging it as an assumption or asking.
- **Boundary.** A value taken from the order record or a retrieved policy is not this mode even when the reply paraphrases it. A policy applied outside its stated scope (refund timing promised for a cancellation) is this mode. Sub-patterns: an interface flow or dashboard path the agent describes with no policy or tool behind it; a refund promise on a cancelled order; an outcome claim made from policy silence.
- **Requirement source.** RESP-3 (RESP-1 when the value is presented as policy)
- **Likely evaluator.** llm judge. **Product change.** Grounding instruction: every number or promise must come from a tool result or a cited policy; ask for missing constraints instead of assuming.
- **Confirmed positives (10).** support-0153 t2, support-0166 t2, support-0113 t3, support-0042 t3, support-0093 t3, support-0093 t4, support-0093 t5, support-0179 t3, support-0191 t2, support-0196 t2
- **Close negatives (4).** support-0092 t2, support-0158 t1, support-0104 t1, support-0150 t2
- **Origin annotations.** `mufqmrs87g1w4`, `mufv2aeub3toc`, `muh06azs1o0me`, `muh02a0u3bq9n`, `muh05bv2bdmt1`, `muh04v0hqzopi`, `muh04995f0yfq`, `muh076uif8vv8`, `muh07tqtrvvr7`, `muh08azb956rf`, `muh03bl8ch4cg`

## Stability check

The final uniform batch (B4-final, 15 traces) produced 2 failures
(support-0098 t1, support-0004 t4), both instances of `duplicate_escalation`,
and **0 new consequential modes**: no note in that batch needed the `new-mode` tag.
No further batch was drawn.

## One taxonomy revision

The candidate `unneeded_escalation` originally held every ticket the reviewer
judged unnecessary: tickets for questions answered from policy, tickets for a
confirmation email, and tickets opened on top of a refund already queued for
human review. The queued-refund tickets turned out to be prompt-compliant: the
system prompt's escalation section asks for them, while `SPEC.md` ESC-1, the
refund and escalation policies, the tool contract, and the scenario
expectations all treat the queue as the human review. The reviewer decided
that the queue is the human review (recorded as revision ESC-1-rev1) and the
mode was split by product change: `unneeded_escalation` (no human was needed;
fix the escalation criteria) and `duplicate_escalation` (a second human-review
item for an issue already under review; fix the prompt and add a code check).
Earlier decisions were re-read after the split: support-0045 t1, first judged
"no failure", flipped to a positive of `unneeded_escalation` to match
support-0161 t1. Two further revisions are recorded in the taxonomy history:
`reply_noise` was narrowed to `raw_record_fields_in_reply` because its
"irrelevant content" clause had become a taste judgment, and the two
search-related candidates were merged out into tool revision TOOL-3-rev1
because one tool fix removes both behaviours.

## Search suggestions: accepted and rejected

37 suggestions were accepted and 7 rejected, each rejection with a reason
(`analysis/state/suggestions.json`). The structural searches found tickets
after queued refunds, second tickets in a session, policy numbers without a
policy id, out-of-scope scenarios without a refusal, data-quality scenarios
whose anomaly went unflagged, and raw field names in replies; a model-assisted
scan then read the 358 turns in no batch for unsupported assertions and
returned 35 candidates, of which the 13 high- and medium-confidence ones were
queued (8 accepted, 5 accepted after a consistency review).

| Suggestion | Trace | Mode | Reason for rejection |
| --- | --- | --- | --- |
| `sugg-0010` | support-0135 t1 | `unneeded_escalation` | It seems appropriate to create a ticket for inconsitent data |
| `sugg-0011` | support-0250 t1 | `unneeded_escalation` | Reviewer: escalating invalid catalog data to a human is appropriate (ESC-3), consistent with the rejection on support-0135. Turn marked no failure; the accepted note was demoted to a comment. |
| `sugg-0021` | support-0035 t1 | `out_of_scope_request_fulfilled` | Refusing a request beyond scope is intended |
| `sugg-0024` | support-0031 t1 | `record_inconsistency_not_surfaced` | the store mismatch is not relevant to this interaction (also I don't see the mismatch, I only see Blue Heron in this interaction so no confusion for the shopper) |
| `sugg-0025` | support-0124 t1 | `record_inconsistency_not_surfaced` | Reviewer: the order record shows Blue Heron Ceramics consistently and the agent checked the store record; a user's claim of a mismatch is not by itself evidence of inconsistent data. No failure. |
| `sugg-0026` | support-0103 t2 | `record_inconsistency_not_surfaced` | there is not apparent mismatch |
| `sugg-0028` | support-0019 t1 | `record_inconsistency_not_surfaced` | Reviewer: stating the missing delivery date and escalating is the correct handling; a different failure on this turn (order date a year before shipment, unstated) is recorded in the reviewer's own note. |

The rejection on support-0135 t1 fixed the boundary of `unneeded_escalation`:
a ticket opened on a record inconsistency the agent cannot resolve is an ESC-3
case and therefore a close negative; the reviewer then reversed an earlier
acceptance on support-0250 t1 (a ticket on an invalid catalog price) to match.

## Specification revisions

Applied to `SPEC.md` on 2026-09-25. The motivating annotation ids refer to
`analysis/state/annotations.json`.

| Revision | Target | Decision | Motivating annotations |
| --- | --- | --- | --- |
| ESC-1-rev1 | SPEC.md ESC-1 | Above the refund threshold, the queued refund is the human review. The agent explains the queued status and does not open a separate support ticket for the same refund. | `mufsrxnlq9lv7` (support-0085 t2), `mufvlhb52bfvr` (support-0228 t3) |
| TOOL-gap-1 | SPEC.md section 4 (tools) | Record a tool gap: no tool lists a store's catalog, and search_products rejects an empty query, so a merchant asking for their lowest-priced listing forces the agent to guess a query (support-0105, Workshop replay W9). Candidate fix: allow an empty query with a store filter, or add a list_store_products tool. No failure mode is defined for it. | `mugh5veed3unw` (support-0105 t1) |
| TOOL-3-rev1 | SPEC.md TOOL-3 / search_products contract | search_products matches every query token as a literal substring, so a plural or variant wording returns zero results for products that exist; the agent then tells the user nothing matches and later contradicts itself. Fix in the tool: normalize singular/plural (and retry tokens) before matching. No failure mode is kept for it. | `mufq10fj1z7eb` (support-0153 t1), `mufwc9vwezgoj` (support-0192 t1), `mufqrzsl80vuy` (support-0192 t3) |
| RESP-6-new | SPEC.md section 6 | Add RESP-6: replies use plain language and never expose raw record field names or internal identifiers from tool results. | `mufr08q4ghlpg` (support-0001 t1) |

## Workshop (Part C)

Ten scenarios were replayed through the instrumented server and inspected in
Raindrop Workshop; nine suggestions were decided (seven accepted, two revised).
Details, run identifiers, the uncertain cases, and the decisions are in
`analysis/report/workshop_notes.md`.

## Preparing for Homework 5

No mode has 30 present labels yet (unneeded_escalation 8, duplicate_escalation 10, out_of_scope_request_fulfilled 5, record_inconsistency_not_surfaced 7, policy_claim_without_citation 12, raw_record_fields_in_reply 4, unsupported_assertion 19); absent labels exceed 100 for every
mode. The judge's mode will be topped up first from the 345 unreviewed traces
(structural candidates and the 22 low-confidence scan hits exist for
`unsupported_assertion` and `unneeded_escalation`), then with synthetic
scenarios as the handout prescribes.
