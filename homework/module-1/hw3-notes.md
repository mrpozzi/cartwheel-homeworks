# Homework 3 notes for the video

Kept by the student and the coding agent while working through `homework/module-1/hw3.md`. Every statement below points at a committed file or a Langfuse trace.

## 1. A pilot scenario that failed (video item 1)

Confirmed failures in `scenarios/pilot_review.jsonl` (5 of 10 reviewed), each with its trace in Langfuse (project `cartwheel-dev`):

| scenario | trace | what happened |
| --- | --- | --- |
| pilot-014 | efedd6a866095f08811a1062ab8e2d90 (turn 1) | Dispute over a "delivered" package: the agent called `issue_refund` ($99.75, auto-approved) and said "I've taken care of it" instead of escalating (ESC-3, RESP-2). |
| pilot-025 | f1f603cdfcb08081bf74b24de8023efc | Day-30 boundary: `get_order` said `refund_eligible: true`, but the reply reasoned "today is September 21, 2026" and declared the window missed. Cartwheel's date is 2026-07-01. |
| pilot-002 | 908c215d8ba8fa1505e3beca78522efa | Reported the right order (880) but added "I don't see an order from a day or two ago", again from the real calendar. |
| pilot-030 | bc292616c64ea17a2846ad5bc7fd4833 | Dispute with no order named: escalated at once without asking which order (RESP-3). |
| pilot-019 | 62001d849a252dda084a372bc39563e2 | Told the merchant product 553 is not in the catalog; `products.id=553` exists (Portable Jam Trio, store 14). `search_products("553")` matches words, not ids. |

Extra metadata and evidence for each are in `scenarios/pilot_scenarios.jsonl` (`expected`) and `scenarios/pilot_review.jsonl` (`evidence`).

## 2. Final scenarios revised after review (video item 2)

Decisions are in `scenarios/support_review.jsonl` (13 accept, 2 revise).

### support-0096 (merchant, order status, 4 turns)

Before: order 5401, status **cancelled**. Turns 3–4 asked "When is it expected to arrive?" and "Is tracking available?", which a merchant would not ask about a cancelled order.

After: order 7919 (Travel Pencil Set, Atlas Stationery, **shipped** 2026-06-26), same merchant, same style, followups regenerated. Expected: `report_status_shipped`.

Before (messages):
  1> I run Atlas Stationery and a customer is already chasing me about order 5401 for the Modern Desk Organizer. What’s the status?
  2> And what was the total charged for that order?
  3> When is it expected to arrive? I need to give them an actual answer.
  4> Is tracking available for it or not?

After (messages):
  1> I need the status for order 7919, Travel Pencil Set. Customer is chasing me and I need an answer now.
  2> Also, what was the total charged on that order?
  3> When is it expected to arrive? I need something clear to tell them.
  4> Is tracking available for it or not?

### support-0118 (product search, 6 turns)

Before: merchant 9007 asking about their own journal listings, with followups "under a lower budget" (no number) and "are any of them from that store I like?", which make no sense for a merchant.

After: shopper 366 searching journals across Cartwheel; followups name a $75 budget and Northwind Books. Expected: `list_matching_products` (13 journals, cheapest first).

Before (messages):
  1> I run Northwind Books and I need to know which of my journal listings are actually live right now and what prices they’re showing at. Can you check the live journals and give me the prices, please? I just need the live ones and the prices.
  2> I need this narrowed down under a lower budget too. Show me the journals that are live but keep it under the lower budget, okay? I’m trying to see the cheaper live options, not the expensive ones.
  3> Actually just tell me the cheapest one. Which live journal is the cheapest, and what’s the price? I need the lowest-priced one, just the cheapest option.
  4> How long would shipping take for that cheapest one? I need the shipping time for it, not the whole list, just that cheapest journal and how fast it ships.
  5> Which one would you pick if you had to choose? I want your recommendation, just pick the best one from the live journal options and say why.
  6> Also, are any of them from that store I like? I want to know if any of the live journal options are from that specific store, so please check that too.

After (messages):
  1> I need a journal. What journal options does Cartwheel have and what are the prices? Please just tell me the journal options and prices.
  2> Okay but which one is the cheapest? I’m trying to get the cheapest journal, so please tell me the lowest-priced one.
  3> I need options under $75. Can you list the journals under $75 only, like just the ones under $75?
  4> Which one would you pick? I want a recommendation, so just tell me which journal you’d choose.
  5> How long would shipping take for that one? I need to know the shipping time for the one you’d pick.
  6> Are any of those journals from Northwind Books? I like that store, so please tell me if any are from Northwind Books.

## 3. Agent change between the pilot and the final run

Commit 96aa1b7 added `Today's date: {today}` to the agent's session context, filled from `db.world_asof` (2026-07-01). Prompt version b7ad7ddefaf5 (pilot) → f45891500f2f (final). Reason: the pilot showed the model reasoning from the real calendar (pilot-025, pilot-002); `SPEC.md` is unchanged.

## 4. Commands for the video

```bash
jq '[.traces[].cartwheel_scenario_id] | unique | length' traces/support_traces.json
```

(Item 3, one complete final trace with its scenario id and tool activity: to be filled in after the final run.)
