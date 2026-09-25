# Workshop notes (Homework 4, Part C)

Raindrop Workshop was installed locally (version 0.1.21, daemon on port 5899)
and wired into the Cartwheel server on 2026-09-25. Ten scenarios from the
Homework 3 dataset were replayed through the server with ids prefixed
`workshop-` so the Homework 4 review set and the Homework 5 trace store never
include them. Every replayed turn reached both destinations: 24 runs in
Workshop and 24 traces in Langfuse.

## How the instrumentation works

- `setup_workshop()` in `observability/instrument.py` runs after the Langfuse
  setup at server startup. When `RAINDROP_LOCAL_DEBUGGER` is set it attaches
  one more batch processor with a standard OTLP/HTTP exporter to the existing
  Langfuse tracer provider. No second provider is created and the Langfuse
  export is untouched. The exporter subclass copies the GenAI tool arguments
  and result into the two attribute names Workshop reads for tool payloads,
  on the exported copy only.
- The dedicated `raindrop-openai-agents` integration was tried first and
  removed: without a cloud write key the Raindrop SDK disables its tracing,
  and its tool tracker depends on that flag, so Workshop showed the model call
  and no tool calls. The OTLP path shows the session root, the agent, every
  model generation with prompt and output, and every tool call with
  arguments and result.
- The Workshop MCP server (`raindrop workshop mcp`) was driven from the
  coding agent to export spans, read run outlines, and record annotations.

## Replayed runs

Replays run against the current development database, which earlier runs
had already mutated: order 7936 (workshop-0010) was already cancelled, so
that replay exercises the already-cancelled path rather than a cancellation.
The replays themselves created refund 606 and tickets 237 to 242.

| Replay id | Turn | Source scenario | Role | Workshop run id | Tools called |
| --- | --- | --- | --- | --- | --- |
| workshop-0001 | 1/1 | support-0007 | shopper | `d502f68414180c7de6dc9b35112939d9` | get_order, issue_refund, search_help_center, get_policy |
| workshop-0002 | 1/3 | support-0036 | merchant | `172218021977cffbdf6f6a413d8d0f76` | get_order |
| workshop-0002 | 2/3 | support-0036 | merchant | `15b5e9d9a772c2ba57a457c63db4b903` | escalate_to_human |
| workshop-0002 | 3/3 | support-0036 | merchant | `7f4fba390c9f1aa145957f762f033e19` | none |
| workshop-0003 | 1/1 | support-0001 | support | `5c8e6c79c8cebc260c37d027187167c2` | get_order, get_store_info |
| workshop-0004 | 1/6 | support-0153 | shopper | `c765acf709968859cee5d666291d1330` | search_products |
| workshop-0004 | 2/6 | support-0153 | shopper | `e50ab32a910e5eb8fc02fc68525160d7` | search_products |
| workshop-0004 | 3/6 | support-0153 | shopper | `14a5f5809ef86d2b3e172897a7358938` | list_my_orders, get_store_info, search_products |
| workshop-0004 | 4/6 | support-0153 | shopper | `6b1ab25499bfcb7ffa4bfe80528c4fc7` | none |
| workshop-0004 | 5/6 | support-0153 | shopper | `edc1d6a5bcbd7bb41705b44d56a74bd7` | none |
| workshop-0004 | 6/6 | support-0153 | shopper | `b8c99b018c5c4b534a63085a34201708` | search_help_center |
| workshop-0005 | 1/3 | support-0139 | shopper | `4083f5003d1b27947c5db406f13b8864` | none |
| workshop-0005 | 2/3 | support-0139 | shopper | `90df2a11f49bab37ace1057fffa40500` | none |
| workshop-0005 | 3/3 | support-0139 | shopper | `64e516c100581c38713151a7aedf3284` | none |
| workshop-0006 | 1/1 | support-0132 | shopper | `c72b24de9fa85e7bdc21ea3a6f1f8d20` | find_order, get_order, get_store_info, get_policy, escalate_to_human |
| workshop-0007 | 1/5 | support-0231 | merchant | `c67bc71ce104014dfc1db0e81e6df59d` | escalate_to_human |
| workshop-0007 | 2/5 | support-0231 | merchant | `d34342da918f3ac393bed64bd8214b79` | none |
| workshop-0007 | 3/5 | support-0231 | merchant | `4a79311e2e85f49ea400583936740b32` | escalate_to_human |
| workshop-0007 | 4/5 | support-0231 | merchant | `39ad732a87b59a695a8629e04950e975` | search_help_center, get_policy, escalate_to_human |
| workshop-0007 | 5/5 | support-0231 | merchant | `2efbf2f6194f9a30783cec6e78f5f20e` | none |
| workshop-0008 | 1/1 | support-0201 | shopper | `d6421dda6228b84c1e2c3a483c6901e5` | search_help_center |
| workshop-0009 | 1/1 | support-0105 | merchant | `9c6b2aa7048451eaf3387a482d08fc47` | search_products, search_products |
| workshop-0010 | 1/2 | support-0243 | shopper | `52f151df9948a8481d358b0063cb992f` | get_order |
| workshop-0010 | 2/2 | support-0243 | shopper | `87f081a0fc0d3acc62aec192cb864278` | escalate_to_human |

## Candidate failures and unusual behaviours

Each item is also saved as a Workshop annotation on the run. Workshop runs
are evidence about the definitions, not review-set traces: they do not count
as positives for Part E.

| Id | Run | What happened | Taxonomy mode |
| --- | --- | --- | --- |
| W1 | workshop-0004 t1 `c765acf7…` | Plural "scarves" returned zero (substring matcher); the agent said nothing matched and suggested "scarf" instead of trying it. Reproduces support-0153 t1; the third replay of this turn to behave this way, so the cause is the tool. | literal_search_reported_as_none |
| W2 | workshop-0004 t2 `e50ab32a…` | "Anything cheaper" after a zero-result turn: the agent invented a $25 ceiling and presented two items as cheaper options. Reproduces support-0153 t2. | unsupported_assertion |
| W3 | workshop-0004 t3 `14a5f580…` | "Store ID: 15" and product ids shown to a shopper. | reply_noise |
| W4 | workshop-0005 t1 to t3 `4083f500…`, `90df2a11…`, `64e516c1…` | Recipes provided in all three turns instead of a refusal. Reproduces support-0139 exactly. | out_of_scope_request_fulfilled |
| W5 | workshop-0007 t3 `4a79311e…` and t4 `39ad732a…` | A second ticket (#240) for the email change already under #239, then a third (#241) for an informational question the agent answered from cw-account-security. | duplicate_escalation, unneeded_escalation |
| W6 | workshop-0010 t2 `87f081a0…` | Ticket #242 opened to send a confirmation email, after the reply states the agent cannot send email. Reproduces support-0243 t2. | unneeded_escalation |
| W7 | workshop-0003 `5c8e6c79…` | Raw field `refund_eligible: false` in the reply to support staff. Reproduces support-0001 t1. | reply_noise |

## Cases where the coding agent is uncertain

- **W8, workshop-0006 `c72b24de…`.** The agent stated that the delivery date
  is missing and escalated (ticket #238), which is the handling the
  taxonomy asks for. It then added an estimated deadline of July 31 from a
  hypothetical "arrived today", hedged as an estimate pending confirmation.
  Whether a hedged hypothetical counts as the workaround that
  record_inconsistency_not_surfaced forbids is a boundary question. The
  reviewer's earlier decision on support-0021 t2 (a table of hypothetical
  deadlines) suggests yes; the escalation suggests the intent was right.
- **W9, workshop-0009 `9c6b2aa7…`.** The agent called the product search
  with an empty query, received invalid_argument, then guessed "ceramic" as
  the query. The "lowest-priced listing" answer is right only because a
  negative price is minimal under any query. This time the reply flags the
  negative price, an improvement over support-0105. Alternative explanation:
  the tool set has no way to list a store's catalog, so the guess is the
  agent working around a tool gap rather than a reasoning failure. That is a
  specification gap (a store catalog listing capability), not a mode.

## Correct handling worth recording

- **W10, workshop-0001 `d502f684…`.** Refund above the threshold queued
  ($101.25, refund 606) with no extra ticket, and cw-refunds cited for the
  timing. Close negative for duplicate_escalation and
  policy_claim_without_citation. The original run of this scenario opened a
  ticket.
- **W11, workshop-0008 `d6421dda…`.** The 30-day default cited with
  cw-returns and the agent asks which store. Close negative for
  policy_claim_without_citation; the original run had no citation.
- **W12, workshop-0002 t1 `17221802…`.** Permission denied handled without
  a ticket; escalation offered, then opened in turn 2 when the merchant
  insisted. The original run escalated immediately.

## Cross-cutting observations

- **Non-determinism.** Seven of ten scenarios used a different tool sequence
  than their original run at the same prompt version. Two original
  positives did not reproduce (W10, W11) and one original failure
  (support-0132) was handled correctly this time. Three modes reproduced
  deterministically: the literal search (tool-side), the out-of-scope
  recipes, and the raw field names. Judges in Homework 5 will see this
  variance; modes describe probabilistic behaviours, not fixed bugs.
- **The 24-hour follow-up promise** appears without a policy id in six
  replayed turns. Under the reviewer's decision on support-0017 these are
  positives of policy_claim_without_citation, which shows how large that
  mode becomes with that boundary.
- **Replay state.** Replays mutate the database (refund 606, tickets 237 to
  242). A later replay pass should restore a pristine copy first.

## Decisions

To be filled by the reviewer for every suggestion discussed in the final
taxonomy: accept, revise, or reject, with a reason.

| Id | Mode touched | Decision | Reason |
| --- | --- | --- | --- |
| W1 | literal_search_reported_as_none | accepted | Three identical replays of the plural query: the mode describes a tool defect (substring matching), so the evaluator can be a code check and the product change is in the tool. |
| W2 | unsupported_assertion | accepted | The invented $25 ceiling recurs at the same prompt; the definition covers it as written. |
| W3 | reply_noise | accepted | Store and product ids shown to a shopper; covered by the definition. |
| W4 | out_of_scope_request_fulfilled | accepted | Deterministic in all three turns; the definition covers it as written. |
| W5 | duplicate_escalation, unneeded_escalation | accepted | Second and third tickets for one request; both definitions apply, and the split between them holds. |
| W6 | unneeded_escalation | accepted | Reproduces the confirmation-email ticket recorded on support-0243; consistent with the reviewer's decision to count it. |
| W7 | reply_noise | accepted | Raw field name in a reply again; covered by the definition. |
| W8 | record_inconsistency_not_surfaced (boundary) | revised | Boundary added: a hedged estimate given after stating the gap and escalating is not the mode; the mode is computing or asking the user instead of escalating. |
| W9 | specification gap (store catalog listing) | revised | Recorded as a tool and specification gap: no tool lists a store's catalog and search_products rejects an empty query, so the agent guessed a query. No new mode. |

Decisions recorded on 2026-09-25 by the reviewer; the coding agent's
recommendations were adopted unchanged.
