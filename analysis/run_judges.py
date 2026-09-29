"""Homework 5: LLM judge for one Cartwheel failure mode.

Exports the HW5 labels, prepares the judge inputs, splits the labels, and
runs the judge on the development and test splits with the Cartwheel helpers.
Run from the repository root::

    uv run python -m analysis.run_judges export            # Part A: HW5 label file (1 = Pass, 0 = Fail)
    uv run python -m analysis.run_judges inputs            # Part B: one judge input per conversation
    uv run python -m analysis.run_judges split             # Part B: train/dev/test split, run once

Every step reads and writes plain files under ``analysis/state/`` so it can
be inspected and resumed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any

from analysis.helpers import _state, split_labels
from analysis.helpers import tools as helper_tools
from analysis.review_app import loader

MODE = "unsupported_assertion"
STATE = _state.state_root()
HW5_LABELS = STATE / "hw5_labels"
INPUTS = STATE / "hw5_trace_inputs.json"
SPLIT_FRACTIONS = (0.20, 0.40, 0.40)
SPLIT_SEED = 7

# The helpers read judge inputs from this export rather than from Langfuse,
# so every prompt version sees exactly the same saved traces.
os.environ.setdefault("CARTWHEEL_JUDGE_TRACE_SOURCE", str(INPUTS))


# ---------------------------------------------------------------------------
# trace index (scenario, session, turn) from the HW4 review cache
# ---------------------------------------------------------------------------


def _sessions() -> list[dict[str, Any]]:
    """The HW4 session view model: turns in order, each with steps and reply."""
    records, _ = loader.load_records(source="auto")
    return loader.build_sessions(records, loader.load_scenarios(), None)


def _trace_index() -> dict[str, dict[str, Any]]:
    """Map every trace id to its session, scenario, turn number, and role."""
    index: dict[str, dict[str, Any]] = {}
    for session in _sessions():
        for turn in session["turns"]:
            index[turn["trace_id"]] = {
                "session_id": session["session_id"],
                "scenario_id": session["scenario_id"],
                "turn": turn["index"],
                "turn_count": session["turn_count"],
                "role": session["role"],
            }
    return index


# ---------------------------------------------------------------------------
# Part A: export the HW5 labels with the Pass = 1 convention
# ---------------------------------------------------------------------------


def _live_hw4_labels(mode: str) -> list[dict[str, Any]]:
    """Current HW4 label per trace (1 = failure present), superseded rows dropped."""
    rows = _state.read_jsonl(STATE / "labels" / f"{mode}.jsonl")
    live: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("superseded_by"):
            continue
        live[row["trace_id"]] = row
    return list(live.values())


def _evidence(trace_id: str, annotations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The reviewer's notes on a trace: failure notes with their quotes, comments, and no-failure marks."""
    out = []
    for ann in annotations:
        if ann.get("trace_id") != trace_id or ann.get("author") == "ai":
            continue
        kind = "no_failure" if ann.get("no_failure") else ("comment" if ann.get("kind") == "comment" else "failure")
        out.append(
            {
                "annotation_id": ann.get("id"),
                "kind": kind,
                "quote": ann.get("quote"),
                "note": ann.get("note"),
                "tags": ann.get("tags") or [],
                "author": ann.get("author"),
            }
        )
    return out


def export_labels(mode: str = MODE) -> Path:
    """Write ``analysis/state/hw5_labels/<mode>.jsonl`` from the HW4 label file.

    One line per labeled trace with ``label`` 1 for Pass (failure absent) and
    0 for Fail (failure present), plus the reviewer's evidence. The HW4 file
    is left untouched; rerun after any label change in the review interface.
    """
    index = _trace_index()
    annotations = _state.read_json(STATE / "annotations.json", default={"annotations": []})["annotations"]
    records = []
    for row in _live_hw4_labels(mode):
        tid = row["trace_id"]
        info = index.get(tid, {})
        records.append(
            {
                "trace_id": tid,
                "mode": mode,
                "label": 1 - int(row["label"]),
                "label_convention": "1 = Pass (failure absent), 0 = Fail (failure present)",
                "session_id": row.get("session_id") or info.get("session_id"),
                "scenario_id": info.get("scenario_id"),
                "turn": info.get("turn"),
                "role": info.get("role"),
                "source": row.get("source"),
                "note": row.get("note"),
                "evidence": _evidence(tid, annotations),
                "hw4_label_id": row.get("label_id"),
                "hw4_ts": row.get("ts"),
            }
        )
    records.sort(key=lambda r: (r["scenario_id"] or "", r["turn"] or 0))
    path = HW5_LABELS / f"{mode}.jsonl"
    _state.write_jsonl(path, records)
    passes = sum(1 for r in records if r["label"] == 1)
    print(f"{path}: {len(records)} labels, {passes} Pass, {len(records) - passes} Fail")
    return path


# ---------------------------------------------------------------------------
# Part B: one judge input per conversation
# ---------------------------------------------------------------------------


def _turn_messages(turn: dict[str, Any]) -> list[dict[str, Any]]:
    """One user turn as judge-facing messages: the request, the agent's
    narration, every tool call with its result, and the reply. The tool name
    travels inside the call and result payloads so it survives the helpers'
    text flattening."""
    messages: list[dict[str, Any]] = [{"role": "user", "text": turn["user"]}]
    for step in turn["steps"]:
        if step.get("narration"):
            messages.append({"role": "assistant", "text": step["narration"]})
        call = step.get("tool_call") or {}
        name = call.get("name")
        messages.append({"role": "tool_call", "name": name, "arguments": {"tool": name, "arguments": call.get("arguments")}})
        result = step.get("tool_result")
        if result is not None:
            messages.append({"role": "tool_result", "name": name, "content": {"tool": name, "result": result.get("output")}})
    if turn["reply"]:
        messages.append({"role": "assistant", "text": turn["reply"]})
    return messages


def prepare_inputs(mode: str = MODE) -> Path:
    """Write ``analysis/state/hw5_trace_inputs.json``: one record per labeled conversation.

    A conversation with a Fail turn ends at its earliest failing turn, so the
    judge evaluates that reply; a Pass-only conversation ends at its last
    labeled turn. Each record holds ``trace_id`` (the evaluated turn) and
    ``trace``: the session context the agent was given, then every earlier
    turn and the evaluated turn with user text, narration, tool calls, tool
    results, and the reply. Labels, reviewer notes, and scenario metadata are
    deliberately absent.
    """
    labels = {row["trace_id"]: int(row["label"]) for row in helper_tools._load_labels(mode)}  # 1 = failure present
    records: list[dict[str, Any]] = []
    fail_records = 0
    for session in _sessions():
        labeled = [t for t in session["turns"] if t["trace_id"] in labels]
        if not labeled:
            continue
        failing = [t for t in labeled if labels[t["trace_id"]] == 1]
        evaluated = failing[0] if failing else labeled[-1]
        fail_records += bool(failing)
        messages: list[dict[str, Any]] = [
            {"role": "context", "text": "Session context given to the agent: " + "; ".join(session["system_prompt"]["context"])}
        ]
        for turn in session["turns"]:
            if turn["index"] > evaluated["index"]:
                break
            messages.extend(_turn_messages(turn))
        records.append({"trace_id": evaluated["trace_id"], "trace": messages})

    ids = [r["trace_id"] for r in records]
    assert len(ids) == len(set(ids)), "duplicate evaluated trace ids"
    assert all(tid in labels for tid in ids), "an input record has no label"
    for record in records:
        assert set(record) == {"trace_id", "trace"}, "input records carry only the trace id and the messages"
    _state.write_json(INPUTS, records)
    sessions_with_labels = len({row["session_id"] for row in _state.read_jsonl(HW5_LABELS / f"{mode}.jsonl")})
    print(
        f"{INPUTS}: {len(records)} records ({fail_records} Fail, {len(records) - fail_records} Pass) "
        f"for {sessions_with_labels} labeled conversations"
    )
    return INPUTS


def split_data(mode: str = MODE, force: bool = False) -> dict[str, list[str]]:
    """Split the HW5 labels into train/dev/test (20/40/40, seed 7), one record per conversation.

    Uses ``split_labels`` restricted to the trace ids in the saved inputs.
    Runs once: an existing split for the mode is kept unless ``force`` is set,
    because the split must stay fixed while the prompt is developed.
    """
    existing = _state.read_json(STATE / "splits.json", default={}).get(mode)
    if existing and not force:
        raise SystemExit(f"a split for '{mode}' already exists in splits.json; pass --force to redo it deliberately")
    records = json.loads(INPUTS.read_text(encoding="utf-8"))
    splits = split_labels(
        mode,
        fractions=SPLIT_FRACTIONS,
        seed=SPLIT_SEED,
        min_per_class=10,
        eligible_trace_ids=[record["trace_id"] for record in records],
    )
    report_split_counts(mode, splits)
    return splits


def report_split_counts(mode: str = MODE, splits: dict[str, list[str]] | None = None) -> dict[str, dict[str, int]]:
    """Print and return Pass/Fail counts per split from the HW5 labels."""
    if splits is None:
        splits = _state.read_json(STATE / "splits.json", default={}).get(mode, {})
    labels = {row["trace_id"]: int(row["label"]) for row in helper_tools._load_labels(mode)}  # 1 = failure present
    counts: dict[str, dict[str, int]] = {}
    for name in ("train", "dev", "test"):
        ids = splits.get(name, [])
        fails = sum(1 for tid in ids if labels[tid] == 1)
        counts[name] = {"pass": len(ids) - fails, "fail": fails}
        print(f"{name:<6} {counts[name]['pass']:>3} Pass  {counts[name]['fail']:>3} Fail")
    return counts


# ---------------------------------------------------------------------------
# Part C: run a prompt version on the development split
# ---------------------------------------------------------------------------

JUDGE_MODEL = "gpt-4o-mini"
REPORT_DIR = Path("analysis/report")


def _metrics_record(judge_id: str, split: str, alignment: dict[str, Any], mode: str) -> dict[str, Any]:
    """The saved metrics: confusion counts, TPR/TNR with Wilson intervals, class counts, disagreements."""
    judge = helper_tools._load_judge(judge_id)
    return {
        "judge_id": judge_id,
        "mode": mode,
        "split": split,
        "model": judge.get("model"),
        "prompt_hash": judge.get("prompt_hash"),
        "prompt_version": judge.get("version"),
        "status": judge.get("status"),
        "convention": "Pass is the positive class (1); TPR = TP/(TP+FN) on human Pass, TNR = TN/(TN+FP) on human Fail",
        "confusion": {"tp": alignment["tp"], "fp": alignment["fp"], "fn": alignment["fn"], "tn": alignment["tn"]},
        "class_counts": {"human_pass": alignment["tp"] + alignment["fn"], "human_fail": alignment["tn"] + alignment["fp"]},
        "tpr": alignment["tpr"],
        "tpr_interval_95": alignment["tpr_interval"],
        "tnr": alignment["tnr"],
        "tnr_interval_95": alignment["tnr_interval"],
        "agreement": alignment["agreement"],
        "n": alignment["n"],
        "disagreements": alignment["disagreements"],
        "computed_at": helper_tools._utcnow(),
    }


def preflight(mode: str = MODE, split: str = "dev") -> dict[str, Any]:
    """What a batch would cost to run: the model and the number of traces not yet cached."""
    splits = _state.read_json(STATE / "splits.json", default={}).get(mode, {})
    ids = splits.get(split, [])
    info = {"mode": mode, "split": split, "model": JUDGE_MODEL, "traces": len(ids), "source": os.environ.get("CARTWHEEL_JUDGE_TRACE_SOURCE")}
    print(f"{split}: {len(ids)} traces on {JUDGE_MODEL}, inputs from {info['source']}")
    return info


def run_development(mode: str = MODE, prompt_path: str | Path = "") -> dict[str, Any]:
    """Register a prompt version, run it on the development split, and save the metrics.

    Registration creates ``analysis/state/judges/<mode>-v<N>.json``; the
    predictions and critiques are cached there by prompt hash, so an
    interrupted run resumes with ``resume_development``. The metrics land in
    ``analysis/report/dev-<judge_id>.json``.
    """
    from observability.instrument import load_env

    load_env()
    from analysis.helpers import judge_alignment, register_judge, run_judge

    prompt_path = Path(prompt_path)
    record = register_judge(mode=mode, prompt_text=prompt_path.read_text(encoding="utf-8"), judge_model=JUDGE_MODEL)
    judge_id = record["judge_id"]
    print(f"registered {judge_id} (prompt {prompt_path.name}, hash {record['prompt_hash']}, model {JUDGE_MODEL})")
    judge = helper_tools._load_judge(judge_id)
    judge["prompt_path"] = str(prompt_path)
    _state.write_json(STATE / "judges" / f"{judge_id}.json", judge)
    run_judge(judge_id, split="dev", batch_size=10)
    development = judge_alignment(judge_id, split="dev")
    report = _metrics_record(judge_id, "dev", development, mode)
    report["prompt_path"] = str(prompt_path)
    out = REPORT_DIR / f"dev-{judge_id}.json"
    _state.write_json(out, report)
    _print_metrics(report)
    print(f"saved {out}")
    return report


def resume_development(judge_id: str) -> dict[str, Any]:
    """Finish an interrupted development run: only uncached traces are classified."""
    from observability.instrument import load_env

    load_env()
    from analysis.helpers import judge_alignment, run_judge

    judge = helper_tools._load_judge(judge_id)
    run_judge(judge_id, split="dev", batch_size=10)
    development = judge_alignment(judge_id, split="dev")
    report = _metrics_record(judge_id, "dev", development, judge["mode"])
    report["prompt_path"] = judge.get("prompt_path")
    out = REPORT_DIR / f"dev-{judge_id}.json"
    _state.write_json(out, report)
    _print_metrics(report)
    print(f"saved {out}")
    return report


def _print_metrics(report: dict[str, Any]) -> None:
    c = report["confusion"]
    print(
        f"{report['judge_id']} on {report['split']}: n={report['n']}  "
        f"TP={c['tp']} FN={c['fn']} TN={c['tn']} FP={c['fp']}  "
        f"TPR={report['tpr']} {report['tpr_interval_95']}  TNR={report['tnr']} {report['tnr_interval_95']}  "
        f"agreement={report['agreement']}  disagreements={len(report['disagreements'])}"
    )


# ---------------------------------------------------------------------------
# Part D: freeze the chosen version and evaluate it once on the test split
# ---------------------------------------------------------------------------


def run_test(judge_id: str) -> dict[str, Any]:
    """Freeze the judge (once), run the held-out test split, and save the metrics.

    Freezing is one-way and unlocks the test split in the helpers. Test
    predictions are cached like the others, so an interrupted run resumes by
    calling this again; the freeze is skipped when already done. The metrics
    land in ``analysis/report/test-<judge_id>.json``.
    """
    from observability.instrument import load_env

    load_env()
    from analysis.helpers import freeze_judge, judge_alignment, run_judge
    from analysis.helpers import guards

    judge = helper_tools._load_judge(judge_id)
    if not guards.is_frozen(judge):
        freeze_judge(judge_id)
        print(f"frozen {judge_id} (prompt hash {judge['prompt_hash']}, model {judge['model']})")
    else:
        print(f"{judge_id} was already frozen at {judge.get('frozen_at')}")
    run_judge(judge_id, split="test", batch_size=10)
    test = judge_alignment(judge_id, split="test")
    report = _metrics_record(judge_id, "test", test, judge["mode"])
    report["prompt_path"] = judge.get("prompt_path")
    report["frozen_at"] = helper_tools._load_judge(judge_id).get("frozen_at")
    out = REPORT_DIR / f"test-{judge_id}.json"
    _state.write_json(out, report)
    _print_metrics(report)
    print(f"saved {out}")
    return report


# ---------------------------------------------------------------------------
# Recalculate metrics from the saved predictions (no model calls)
# ---------------------------------------------------------------------------


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Two-sided Wilson score interval for a binomial rate, written out step by step."""
    if total == 0:
        return (float("nan"), float("nan"))
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half_width = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, center - half_width), min(1.0, center + half_width))


def recalculate(judge_id: str, split: str = "test") -> dict[str, Any]:
    """Recompute the confusion counts, TPR, TNR, and Wilson intervals from the saved files.

    Reads the judge record's cached verdicts (1 = Pass) and the HW5 label
    file (1 = Pass) for the split's trace ids, and prints every step. This is
    the on-camera check: it needs no model and no network.
    """
    judge = helper_tools._load_judge(judge_id)
    verdicts = judge["predictions"][judge["prompt_hash"]]  # stored with 1 = Pass for HW5 judges
    labels = {row["trace_id"]: int(row["label"]) for row in _state.read_jsonl(HW5_LABELS / f"{judge['mode']}.jsonl")}
    ids = _state.read_json(STATE / "splits.json", default={})[judge["mode"]][split]
    tp = fn = tn = fp = 0
    for tid in ids:
        human_pass, judge_pass = labels[tid] == 1, int(verdicts[tid]) == 1
        if human_pass and judge_pass:
            tp += 1
        elif human_pass:
            fn += 1
        elif judge_pass:
            fp += 1
        else:
            tn += 1
    tpr, tnr = tp / (tp + fn), tn / (tn + fp)
    tpr_ci, tnr_ci = wilson_interval(tp, tp + fn), wilson_interval(tn, tn + fp)
    print(f"{judge_id} on {split}: {len(ids)} traces, status {judge.get('status')}, model {judge.get('model')}, prompt hash {judge['prompt_hash']}")
    print(f"  human Pass = {tp + fn}, human Fail = {tn + fp}")
    print(f"  judge Pass on human Pass (TP) = {tp}    judge Fail on human Pass (FN) = {fn}")
    print(f"  judge Fail on human Fail (TN) = {tn}    judge Pass on human Fail (FP) = {fp}")
    print(f"  TPR = TP / (TP + FN) = {tp} / {tp + fn} = {tpr:.4f}   95% Wilson interval [{tpr_ci[0]:.4f}, {tpr_ci[1]:.4f}]")
    print(f"  TNR = TN / (TN + FP) = {tn} / {tn + fp} = {tnr:.4f}   95% Wilson interval [{tnr_ci[0]:.4f}, {tnr_ci[1]:.4f}]")
    return {"tp": tp, "fn": fn, "tn": tn, "fp": fp, "tpr": tpr, "tnr": tnr, "tpr_interval": tpr_ci, "tnr_interval": tnr_ci}


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["export", "inputs", "split", "counts", "preflight", "dev", "resume-dev", "test", "metrics"])
    parser.add_argument("target", nargs="?", help="prompt path for dev; judge id for resume-dev, test, and metrics")
    parser.add_argument("--mode", default=MODE)
    parser.add_argument("--split", default="dev", help="split for preflight and metrics")
    parser.add_argument("--force", action="store_true", help="redo an existing split (breaks comparability with earlier runs)")
    args = parser.parse_args()
    if args.command == "export":
        export_labels(args.mode)
    elif args.command == "inputs":
        prepare_inputs(args.mode)
    elif args.command == "split":
        split_data(args.mode, force=args.force)
    elif args.command == "counts":
        report_split_counts(args.mode)
    elif args.command == "preflight":
        preflight(args.mode, args.split)
    elif args.command == "dev":
        if not args.target:
            raise SystemExit("dev needs a prompt path, e.g. analysis/prompts/unsupported_assertion-v0.txt")
        run_development(args.mode, args.target)
    elif args.command == "resume-dev":
        if not args.target:
            raise SystemExit("resume-dev needs a judge id, e.g. unsupported_assertion-v0")
        resume_development(args.target)
    elif args.command == "test":
        if not args.target:
            raise SystemExit("test needs the judge id to freeze and evaluate, e.g. unsupported_assertion-v2")
        run_test(args.target)
    elif args.command == "metrics":
        if not args.target:
            raise SystemExit("metrics needs a judge id, e.g. unsupported_assertion-v3 --split test")
        recalculate(args.target, args.split)


if __name__ == "__main__":
    main()
