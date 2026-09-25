# Interface comparison (Homework 4, Part A)

The review interface lives under `analysis/review_app/` and is compared here
with the reference interface (`analysis/server.py`, `analysis/ui/index.html`).

## Trace source

Traces are loaded live from the local Langfuse project (466 traces, 250
sessions, filtered to the `support-*` scenario ids) and cached under a
gitignored folder. The Module 1 export `traces/support_traces.json` is wired
as the offline fallback, and the interface records which source was used and
why. The fallback was not needed during this assignment.

## Observations from the standard Langfuse view

Before designing, 7 traces were reviewed in Langfuse's trace and annotation
views. Recorded friction:

- A tool result sat about three clicks from the reply that described it.
- No path led from a trace to the earlier or later turn of the same
  conversation. Langfuse's own session field is empty on all 466 traces; the
  session id exists only inside metadata attributes, so the Sessions page
  could not group them.
- The model generation's input was drowned by the system prompt, which is
  identical across traces except three session context lines.
- The Annotate panel asked about score configs before a note could be taken.
- One trace took several minutes and still felt incomplete.

## One design retained from the reference interface

The annotation model: select text in the trace, type a free-text note, and
see it as a margin card aligned with the highlight. The reference's
pending-highlight trick (wrap the selection before focusing the input so the
browser does not clear it) and its file-backed JSON API with atomic writes
were kept as they were. AI suggestions stay visually distinct (dashed
highlight, separate card) and need an explicit accept or reject, as in the
reference.

## One design changed after inspecting the traces

The unit of display became the session. Cartwheel writes one Langfuse trace
per user turn, and the reference flattened each trace into a list of messages.
The supplied normalizer also merges multi-turn traces under the first turn's
id, which would break per-trace scores. The new loader groups traces by
`cartwheel.session_id`, orders turns by timestamp, and groups each turn's
observations into steps (the model's narration sentence, the tool call, and
the tool result in one indented container), followed by the reply. Generation
inputs are not rendered because the timeline already shows every message they
contain; the system prompt is shown once per session, collapsed, with only its
context lines visible. Outcome badges computed from tool results (permission
denied, refund queued or auto-approved, cancelled, escalated with ticket id)
and a drawer listing the `SPEC.md` requirement ids were added so the
consequential step and the requirement to cite are visible before reading.

Additions made during use: collapsible turns that keep the user message and a
one-line summary, a "failure observed" button that opens a note anchored to
the whole turn beside the "no failure" record, component-anchored notes with
tags, per-trace and per-session comments, sidebar ordering (including a
seeded reshuffle) and per-session review status, and tool chips that filter
the sidebar.

## Limitations remaining

1. The model's actual input is not shown. The timeline reconstructs the
   context from the session's turns and tool results. If the runtime pruned
   history or truncated a tool result, the reviewer would not see it, which
   matters for the first-failure rule.
2. Open codes, tags, and comments live only in `analysis/state/`. Only the
   binary labels reach Langfuse as scores, so Langfuse alone shows verdicts
   without the observations behind them. The interface also assumes a single
   reviewer: notes carry an `author` field but there is no per-person
   identity or agreement measure.
3. Tags are free text with autocomplete but no controlled vocabulary. A typo
   creates a second tag and splits a search; this happened once during the
   smoke test.
4. Found during review, and an instance of limitation 1: when the agent issued
   several calls to the same tool in one step, the loader paired results with
   calls by position, and Langfuse does not guarantee that order, so one turn
   showed the "neck warmer" search with the "scarf" result. Results are now
   matched by their arguments (verified on all 772 tool steps). The defect
   affected 53 turns and one verdict was re-checked; it did not change.
