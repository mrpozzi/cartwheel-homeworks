"""Homework 7 monitor: sample one period's conversations and run the frozen judge.

Run from the repository root::

    uv run python -m monitoring.run --period before --dry-run   # counts only, no judge calls
    uv run python -m monitoring.run --period before
    uv run python -m monitoring.run --last-hours 24             # the scheduled job

A period is a time window in ``monitoring/config.json``. The monitor fetches
the window's traces from Langfuse, keeps the traces of the 50 monitoring
scenarios, and rejects the period when a scenario is missing, a scenario was
retried in a second session, or a trace used another Cartwheel model. Each
scenario becomes one conversation record whose id is its final trace id. The
judge text is built exactly as Homework 5 built it, so the frozen judge sees
the inputs it was validated on. The random sample and the risk groups are
judged together once and saved separately; only the random sample estimates
the failure rate.

``--last-hours N`` monitors live traffic instead: every conversation (grouped
by ``meta.session_id``) whose final trace falls in the last N hours, with its
earlier turns fetched from a lookback window. Conversations on another
Cartwheel model are skipped and counted. An empty window records a zero count
and exits without calling the judge.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from analysis.helpers.normalization import normalize_trace
from analysis.review_app import loader
from analysis.run_judges import _turn_messages
from monitoring.sample import DEFAULT_RISK_GROUPS, select_traces

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "monitoring" / "config.json"
SCENARIOS_PATH = REPO_ROOT / "scenarios" / "monitoring_scenarios.jsonl"
OUTPUT_DIR = REPO_ROOT / "monitoring" / "output"
HISTORY_PATH = REPO_ROOT / "monitoring" / "history.jsonl"
CHART_PATH = REPO_ROOT / "monitoring" / "prevalence.svg"
SAMPLE_SEED = 7
MONITOR_TRACE_NAME = "hw7-monitor"
# How far before a window to look for the earlier turns of its conversations.
LOOKBACK_HOURS = 24


class PeriodRejected(ValueError):
    """The window is not one complete, single-model run of the scenario set."""


# ---------------------------------------------------------------------------
# configuration and inputs
# ---------------------------------------------------------------------------


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    unknown = set(config["risk_groups"]) - set(DEFAULT_RISK_GROUPS)
    if unknown:
        raise ValueError(f"unknown risk groups in config: {sorted(unknown)}")
    return config


def scenario_ids(path: Path = SCENARIOS_PATH) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["id"] for line in lines if line.strip()]


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def model_matches(observed: str, configured: str) -> bool:
    """The configured model name, or a dated snapshot of it (gpt-5.5-2026-04-23)."""
    return observed == configured or bool(
        re.fullmatch(re.escape(configured) + r"-\d{4}-\d{2}-\d{2}", observed)
    )


# ---------------------------------------------------------------------------
# Langfuse
# ---------------------------------------------------------------------------


def fetch_window(start: datetime, end: datetime, keep_scenarios: set[str] | None) -> tuple[list[dict[str, Any]], int]:
    """Full trace records in ``[start, end)`` and the window's total trace count.

    Trace summaries carry metadata, so only traces of ``keep_scenarios`` are
    fetched in full; with None, every trace that carries a Cartwheel session id.
    """
    from langfuse import Langfuse

    client = Langfuse()
    summaries: list[Any] = []
    page = 1
    while True:
        response = client.api.trace.list(page=page, limit=100, from_timestamp=start, to_timestamp=end)
        batch = list(response.data or [])
        # The monitor's own traces are never part of a monitored window.
        summaries.extend(s for s in batch if getattr(s, "name", None) != MONITOR_TRACE_NAME)
        if len(batch) < 100:
            break
        page += 1

    records: list[dict[str, Any]] = []
    for summary in summaries:
        attrs = loader._attrs(loader._jsonable(getattr(summary, "metadata", None)))
        scenario = attrs.get("cartwheel.scenario_id")
        if keep_scenarios is None and not attrs.get("cartwheel.session_id"):
            continue
        if keep_scenarios is not None and scenario not in keep_scenarios:
            continue
        full = loader._jsonable(client.api.trace.get(summary.id))
        if scenario:
            full.setdefault("cartwheel_scenario_id", str(scenario))
        records.append(full)
    return records, len(summaries)


# ---------------------------------------------------------------------------
# conversation records
# ---------------------------------------------------------------------------


def conversation_text(session: dict[str, Any], upto: int | None = None) -> str:
    """The judge text for one conversation, built as Homework 5 built it.

    The session context line, then each turn's request, narration, tool calls
    and results (tool name inside the payload), and reply, flattened by the
    shared normalizer. ``upto`` stops after that turn index; the monitor
    passes None so the judge evaluates the conversation's final reply.
    """
    messages: list[dict[str, Any]] = [
        {"role": "context", "text": "Session context given to the agent: " + "; ".join(session["system_prompt"]["context"])}
    ]
    for turn in session["turns"]:
        if upto is not None and turn["index"] > upto:
            break
        messages.extend(_turn_messages(turn))
    return normalize_trace({"trace_id": session["trace_ids"][-1], "trace": messages})["text"]


def _generation_models(turn: dict[str, Any]) -> list[str]:
    return [str(model) for model in turn.get("models") or []]


def build_period_records(
    records: list[dict[str, Any]], expected_ids: list[str], model: str
) -> list[dict[str, Any]]:
    """One conversation record per scenario, validated against the scenario set and model."""
    sessions = loader.build_sessions(records, {}, None)

    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for session in sessions:
        by_scenario.setdefault(str(session["scenario_id"]), []).append(session)
    missing = [sid for sid in expected_ids if sid not in by_scenario]
    if missing:
        raise PeriodRejected(f"{len(missing)} scenario ids are missing from the window: {missing[:5]}")
    retried = {sid: len(group) for sid, group in by_scenario.items() if len(group) > 1}
    if retried:
        raise PeriodRejected(f"scenarios with more than one session (retries) in the window: {retried}")

    bad_models = {}
    for session in sessions:
        for turn in session["turns"]:
            observed = _generation_models(turn)
            if not observed or not all(model_matches(m, model) for m in observed):
                bad_models[turn["trace_id"]] = observed
    if bad_models:
        sample = dict(list(bad_models.items())[:5])
        raise PeriodRejected(f"{len(bad_models)} traces did not run on {model}: {sample}")

    return [_conversation_record(by_scenario[sid][0]) for sid in sorted(expected_ids)]


def _conversation_record(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": session["trace_ids"][-1],
        "timestamp": session["turns"][-1]["timestamp"],
        "scenario_id": session["scenario_id"],
        "session_id": session["session_id"],
        "trace_ids": session["trace_ids"],
        "tools": session["tools"],
        "turn_count": session["turn_count"],
        "models": sorted({m for turn in session["turns"] for m in _generation_models(turn)}),
        "prompt_version": session["prompt_version"],
        "text": conversation_text(session),
    }


def build_window_records(
    records: list[dict[str, Any]], start: datetime, end: datetime, model: str
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Conversations whose final trace falls in ``[start, end)``, on the configured model.

    ``records`` should include a lookback before ``start`` so a conversation
    that began earlier keeps its first turns. Conversations that used another
    model are skipped and counted by model.
    """
    conversations: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}
    for session in loader.build_sessions(records, {}, None):
        if not start <= _parse_utc(session["turns"][-1]["timestamp"]) < end:
            continue
        observed = [m for turn in session["turns"] for m in _generation_models(turn)]
        if not observed or not all(model_matches(m, model) for m in observed):
            key = ", ".join(sorted(set(observed))) or "no model recorded"
            skipped[key] = skipped.get(key, 0) + 1
            continue
        conversations.append(_conversation_record(session))
    conversations.sort(key=lambda c: (c["timestamp"], c["id"]))
    return conversations, skipped


# ---------------------------------------------------------------------------
# one monitoring run
# ---------------------------------------------------------------------------


def _selection_summary(plan: dict[str, Any]) -> dict[str, Any]:
    risk_ids = {t["id"] for group in plan["risk_groups"].values() for t in group}
    random_ids = {t["id"] for t in plan["random"]}
    return {
        "random_sample": len(plan["random"]),
        "risk_groups": {name: len(group) for name, group in plan["risk_groups"].items()},
        "risk_sample": len(risk_ids),
        "in_both": len(random_ids & risk_ids),
        "judge_calls": len(plan["to_judge"]),
    }


def write_monitor_trace(label: str, period_end: datetime, summary: dict[str, Any]) -> str:
    """Create or update the period's monitor trace and return its stable id.

    Langfuse v3 attaches every score to exactly one trace, session, or dataset
    run, so the period's prevalence score lives on one small trace per period,
    dated at the period end and carrying the period's history line.
    """
    import uuid

    from langfuse import Langfuse

    from monitoring.write_scores import _stable_id

    trace_id = _stable_id(MONITOR_TRACE_NAME, label)
    event = {
        "id": str(uuid.uuid4()),
        "type": "trace-create",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "body": {
            "id": trace_id,
            "name": MONITOR_TRACE_NAME,
            "timestamp": period_end.isoformat(),
            "metadata": summary,
            "tags": [MONITOR_TRACE_NAME],
        },
    }
    response = loader._jsonable(Langfuse().api.ingestion.batch(batch=[event]))
    if response.get("errors"):
        raise RuntimeError(f"Langfuse rejected the monitor trace: {response['errors']}")
    return trace_id


def dated_score_records(
    result: dict[str, Any], estimate: dict[str, Any], period_end: datetime, monitor_trace_id: str
) -> list[dict[str, Any]]:
    """The score records, each dated at its trace (verdicts) or the period end (prevalence)."""
    from monitoring.write_scores import build_score_records

    records = build_score_records(
        result["judge_mode"], result["random_verdicts"], result["risk_verdicts"], estimate, result["period"]
    )
    trace_time = {c["id"]: c["timestamp"] for c in result["conversations"]}
    for record in records:
        if record["trace_id"] is None:
            record["trace_id"] = monitor_trace_id
            record["timestamp"] = period_end
        else:
            record["timestamp"] = _parse_utc(trace_time[record["trace_id"]])
    return records


def verify_scores(records: list[dict[str, Any]], timeout_s: float = 120.0) -> dict[str, Any]:
    """Read the written scores back from Langfuse (ingestion is asynchronous).

    Reports scores still missing, values or timestamps that differ from the
    records, and any trace carrying more than one score of the same name.
    """
    from langfuse import Langfuse

    client = Langfuse()
    wanted = {r["score_id"]: r for r in records}
    names = sorted({r["name"] for r in records})
    deadline = time.monotonic() + timeout_s
    while True:
        seen: list[dict[str, Any]] = []
        for name in names:
            page = 1
            while True:
                response = loader._jsonable(client.api.score_v_2.get(name=name, page=page, limit=100))
                seen.extend(response["data"])
                if page >= response["meta"]["totalPages"]:
                    break
                page += 1
        by_id = {s["id"]: s for s in seen}
        missing = [sid for sid in wanted if sid not in by_id]
        if not missing or time.monotonic() > deadline:
            break
        time.sleep(3)

    found = [by_id[sid] for sid in wanted if sid in by_id]
    value_mismatch = [s["id"] for s in found if abs(float(s["value"]) - wanted[s["id"]]["value"]) > 1e-9]
    # Langfuse stores milliseconds.
    time_mismatch = [
        s["id"] for s in found
        if abs((_parse_utc(s["timestamp"]) - wanted[s["id"]]["timestamp"]).total_seconds()) >= 0.001
    ]
    ours = {(r["name"], r["trace_id"]) for r in records if r["trace_id"]}
    per_trace: dict[tuple[str, str], int] = {}
    for s in seen:
        key = (s["name"], s.get("traceId"))
        if key in ours:
            per_trace[key] = per_trace.get(key, 0) + 1
    duplicates = {f"{name} {tid}": n for (name, tid), n in per_trace.items() if n > 1}
    return {
        "written": len(records),
        "found": len(found),
        "missing": missing,
        "value_mismatch": value_mismatch,
        "timestamp_mismatch": time_mismatch,
        "duplicate_scores": duplicates,
    }


def update_history(entry: dict[str, Any], path: Path = HISTORY_PATH) -> list[dict[str, Any]]:
    """One line per period: a rerun replaces that period's line."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    history = [json.loads(line) for line in lines if line.strip()]
    for i, old in enumerate(history):
        if old["period"] == entry["period"]:
            history[i] = entry
            break
    else:
        history.append(entry)
    path.write_text("".join(json.dumps(h, ensure_ascii=False) + "\n" for h in history), encoding="utf-8")
    return history


def write_chart(history: list[dict[str, Any]], config: dict[str, Any], path: Path = CHART_PATH) -> None:
    from monitoring.chart import prevalence_chart

    order = {p["label"]: i for i, p in enumerate(config["periods"])}
    points = [
        {"label": h["period"], "corrected": h["corrected"], "ci_low": h["ci_low"], "ci_high": h["ci_high"]}
        for h in sorted(history, key=lambda h: (order.get(h["period"], len(order)), h["to"]))
        if h.get("corrected") is not None  # an empty window has no estimate
    ]
    if not points:
        return
    path.write_text(prevalence_chart(points, config["threshold"], config["judge_mode"]) + "\n", encoding="utf-8")


def monitor_conversations(
    label: str,
    window: tuple[str, str],
    conversations: list[dict[str, Any]],
    config: dict[str, Any],
    counts: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Sample, judge, correct, score, and record one batch of conversations."""
    from monitoring.run_judges import judge_sample, load_monitoring_judge

    groups = {name: DEFAULT_RISK_GROUPS[name] for name in config["risk_groups"]}
    plan = select_traces(conversations, config["random_rate"], groups, seed=SAMPLE_SEED)
    selection = _selection_summary(plan)
    judge = load_monitoring_judge(config["judge_id"])

    print(f"  conversations: {len(conversations)}  models: {sorted({m for c in conversations for m in c['models']})}")
    print(f"  random sample: {selection['random_sample']}  risk groups: {selection['risk_groups']}  in both: {selection['in_both']}")
    print(f"  judge {judge['judge_id']} on {judge['model']}: {selection['judge_calls']} judge calls")

    result: dict[str, Any] = {
        "period": label,
        "from": window[0],
        "to": window[1],
        "judge_id": judge["judge_id"],
        "judge_model": judge["model"],
        "judge_mode": config["judge_mode"],
        "model": config["model"],
        "observed_models": sorted({m for c in conversations for m in c["models"]}),
        "prompt_versions": sorted({str(c["prompt_version"]) for c in conversations}),
        "counts": {
            **counts,
            "langfuse_traces": sum(len(c["trace_ids"]) for c in conversations),
            "conversations": len(conversations),
            **selection,
        },
        "sample_seed": SAMPLE_SEED,
        "conversations": [
            {key: c[key] for key in ("id", "timestamp", "scenario_id", "session_id", "turn_count", "tools")}
            for c in conversations
        ],
        "random_ids": [t["id"] for t in plan["random"]],
        "risk_group_ids": {name: [t["id"] for t in group] for name, group in plan["risk_groups"].items()},
    }
    if dry_run:
        print("  dry run: no judge calls made")
        return result

    verdicts = judge_sample(config["judge_id"], plan["to_judge"])
    risk_ids = list(dict.fromkeys(tid for ids in result["risk_group_ids"].values() for tid in ids))
    result["random_verdicts"] = {tid: verdicts[tid] for tid in result["random_ids"]}
    result["risk_verdicts"] = {tid: verdicts[tid] for tid in risk_ids}
    result["critiques"] = {tid: verdicts.critiques.get(tid) for tid in verdicts}
    result["judged_at"] = datetime.now(timezone.utc).isoformat()

    # Only the random sample estimates the failure rate.
    from monitoring.correct import corrected_mode_prevalence
    from monitoring.run_judges import judge_test_data
    from monitoring.write_scores import post_scores

    estimate = corrected_mode_prevalence(
        [result["random_verdicts"][tid] for tid in result["random_ids"]], *judge_test_data(config["judge_id"])
    )
    result["estimate"] = estimate
    entry = _history_entry(result, config) | {
        "random_flagged": sum(result["random_verdicts"].values()),
        "risk_flagged": sum(result["risk_verdicts"].values()),
        "raw": estimate["raw"],
        "corrected": estimate["corrected"],
        "ci_low": estimate["ci_low"],
        "ci_high": estimate["ci_high"],
        "confidence": estimate["confidence"],
        "failure_sensitivity": estimate["failure_sensitivity"],
        "pass_specificity": estimate["pass_specificity"],
        "threshold": config["threshold"],
        "judged_at": result["judged_at"],
    }
    window_end = _parse_utc(window[1])
    monitor_trace_id = write_monitor_trace(label, window_end, entry)
    records = dated_score_records(result, estimate, window_end, monitor_trace_id)
    post_scores(records)
    result["monitor_trace_id"] = monitor_trace_id
    result["scores"] = verify_scores(records)

    out = _save(result, entry, config)
    scores = result["scores"]
    print(f"  random flagged {entry['random_flagged']}/{len(result['random_verdicts'])}, risk flagged {entry['risk_flagged']}/{len(result['risk_verdicts'])}")
    print(f"  raw {estimate['raw']}  corrected {estimate['corrected']}  {int(estimate['confidence'] * 100)}% CI [{estimate['ci_low']}, {estimate['ci_high']}]")
    print(
        f"  Langfuse scores: {scores['found']}/{scores['written']} read back, missing {len(scores['missing'])}, "
        f"value mismatches {len(scores['value_mismatch'])}, timestamp mismatches {len(scores['timestamp_mismatch'])}, "
        f"duplicates {len(scores['duplicate_scores'])}"
    )
    print(f"  saved {_rel(out)}, {_rel(HISTORY_PATH)}, {_rel(CHART_PATH)}")
    return result


def _history_entry(result: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    return {
        "period": result["period"],
        "from": result["from"],
        "to": result["to"],
        "judge_id": result["judge_id"],
        "judge_model": result["judge_model"],
        "model": result["model"],
        "observed_models": result["observed_models"],
        "prompt_versions": result["prompt_versions"],
        **{key: result["counts"][key] for key in (
            "langfuse_traces", "conversations", "random_sample", "risk_sample", "risk_groups", "judge_calls"
        )},
        **({"skipped_other_models": result["counts"]["skipped_other_models"]}
           if "skipped_other_models" in result["counts"] else {}),
    }


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)


def _save(result: dict[str, Any], entry: dict[str, Any], config: dict[str, Any]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / f"{result['period']}.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_chart(update_history(entry, HISTORY_PATH), config, CHART_PATH)
    return out


def run_period(label: str, config: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    """One configured comparison period: the 50 scenarios, complete and on one model."""
    period = next((p for p in config["periods"] if p["label"] == label), None)
    if period is None:
        raise ValueError(f"no period {label!r} in {CONFIG_PATH.name}")
    expected = scenario_ids()
    records, window_total = fetch_window(_parse_utc(period["from"]), _parse_utc(period["to"]), set(expected))
    conversations = build_period_records(records, expected, config["model"])
    if len(conversations) != len(expected):
        raise PeriodRejected(f"expected {len(expected)} conversation records, built {len(conversations)}")

    print(f"period {label}: {period['from']} -> {period['to']}")
    print(f"  Langfuse traces: {window_total} in the window, {len(records)} from the {len(expected)} scenarios")
    return monitor_conversations(
        label, (period["from"], period["to"]), conversations, config, {"window_traces": window_total}, dry_run
    )


def run_window(hours: float, config: dict[str, Any], dry_run: bool = False, now: datetime | None = None) -> dict[str, Any]:
    """The scheduled job: conversations whose final trace is in the last ``hours`` hours."""
    end = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    start = end - timedelta(hours=hours)
    label = f"daily-{end:%Y-%m-%d}"
    window = (start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z"))
    records, fetched = fetch_window(start - timedelta(hours=LOOKBACK_HOURS), end, None)
    conversations, skipped = build_window_records(records, start, end, config["model"])

    print(f"window {label}: {window[0]} -> {window[1]} (lookback {LOOKBACK_HOURS} h for earlier turns)")
    print(f"  Langfuse traces fetched with the lookback: {fetched}; conversations on another model skipped: {skipped or 0}")
    counts = {"fetched_traces": fetched, "skipped_other_models": sum(skipped.values())}
    if conversations:
        return monitor_conversations(label, window, conversations, config, counts, dry_run)

    print("  no eligible conversations: recording a zero count, no judge calls")
    result = {
        "period": label,
        "from": window[0],
        "to": window[1],
        "judge_id": config["judge_id"],
        "judge_model": None,
        "judge_mode": config["judge_mode"],
        "model": config["model"],
        "observed_models": [],
        "prompt_versions": [],
        "counts": counts | {"langfuse_traces": 0, "conversations": 0, "random_sample": 0,
                            "risk_groups": {name: 0 for name in config["risk_groups"]},
                            "risk_sample": 0, "in_both": 0, "judge_calls": 0},
        "skipped_by_model": skipped,
    }
    if dry_run:
        return result
    from monitoring.run_judges import load_monitoring_judge

    result["judge_model"] = load_monitoring_judge(config["judge_id"])["model"]
    entry = _history_entry(result, config) | {
        "random_flagged": 0, "risk_flagged": 0, "raw": None, "corrected": None, "ci_low": None,
        "ci_high": None, "threshold": config["threshold"],
        "judged_at": datetime.now(timezone.utc).isoformat(),
    }
    out = _save(result, entry, config)
    print(f"  saved {_rel(out)}, {_rel(HISTORY_PATH)}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--period", help="a period label from monitoring/config.json")
    target.add_argument("--last-hours", type=float, help="monitor the conversations that ended in the last N hours")
    parser.add_argument("--dry-run", action="store_true", help="fetch, validate, and sample without calling the judge")
    args = parser.parse_args(argv)
    if args.last_hours is not None and args.last_hours <= 0:
        parser.error("--last-hours must be positive")

    from observability.instrument import load_env

    load_env()
    config = load_config()
    try:
        if args.period:
            run_period(args.period, config, dry_run=args.dry_run)
        else:
            run_window(args.last_hours, config, dry_run=args.dry_run)
    except PeriodRejected as exc:
        print(f"period {args.period} rejected: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
