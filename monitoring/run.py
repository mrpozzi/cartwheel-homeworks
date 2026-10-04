"""Homework 7 monitor: sample one period's conversations and run the frozen judge.

Run from the repository root::

    uv run python -m monitoring.run --period before --dry-run   # counts only, no judge calls
    uv run python -m monitoring.run --period before

A period is a time window in ``monitoring/config.json``. The monitor fetches
the window's traces from Langfuse, keeps the traces of the 50 monitoring
scenarios, and rejects the period when a scenario is missing, a scenario was
retried in a second session, or a trace used another Cartwheel model. Each
scenario becomes one conversation record whose id is its final trace id. The
judge text is built exactly as Homework 5 built it, so the frozen judge sees
the inputs it was validated on. The random sample and the risk groups are
judged together once and saved separately; only the random sample estimates
the failure rate.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
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
SAMPLE_SEED = 7


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
    fetched in full (all traces when it is None).
    """
    from langfuse import Langfuse

    client = Langfuse()
    summaries: list[Any] = []
    page = 1
    while True:
        response = client.api.trace.list(page=page, limit=100, from_timestamp=start, to_timestamp=end)
        batch = list(response.data or [])
        summaries.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    records: list[dict[str, Any]] = []
    for summary in summaries:
        attrs = loader._attrs(loader._jsonable(getattr(summary, "metadata", None)))
        scenario = attrs.get("cartwheel.scenario_id")
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

    conversations = []
    for sid in sorted(expected_ids):
        session = by_scenario[sid][0]
        conversations.append(
            {
                "id": session["trace_ids"][-1],
                "scenario_id": sid,
                "session_id": session["session_id"],
                "trace_ids": session["trace_ids"],
                "tools": session["tools"],
                "turn_count": session["turn_count"],
                "models": sorted({m for turn in session["turns"] for m in _generation_models(turn)}),
                "prompt_version": session["prompt_version"],
                "text": conversation_text(session),
            }
        )
    return conversations


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


def run_period(label: str, config: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    from monitoring.run_judges import judge_sample, load_monitoring_judge

    period = next((p for p in config["periods"] if p["label"] == label), None)
    if period is None:
        raise ValueError(f"no period {label!r} in {CONFIG_PATH.name}")
    expected = scenario_ids()
    records, window_total = fetch_window(_parse_utc(period["from"]), _parse_utc(period["to"]), set(expected))
    conversations = build_period_records(records, expected, config["model"])
    if len(conversations) != len(expected):
        raise PeriodRejected(f"expected {len(expected)} conversation records, built {len(conversations)}")

    groups = {name: DEFAULT_RISK_GROUPS[name] for name in config["risk_groups"]}
    plan = select_traces(conversations, config["random_rate"], groups, seed=SAMPLE_SEED)
    selection = _selection_summary(plan)
    judge = load_monitoring_judge(config["judge_id"])

    print(f"period {label}: {period['from']} -> {period['to']}")
    print(f"  Langfuse traces: {window_total} in the window, {len(records)} from the {len(expected)} scenarios")
    print(f"  conversations: {len(conversations)}  models: {sorted({m for c in conversations for m in c['models']})}")
    print(f"  random sample: {selection['random_sample']}  risk groups: {selection['risk_groups']}  in both: {selection['in_both']}")
    print(f"  judge {judge['judge_id']} on {judge['model']}: {selection['judge_calls']} judge calls")

    result: dict[str, Any] = {
        "period": label,
        "from": period["from"],
        "to": period["to"],
        "judge_id": judge["judge_id"],
        "judge_model": judge["model"],
        "judge_mode": config["judge_mode"],
        "model": config["model"],
        "observed_models": sorted({m for c in conversations for m in c["models"]}),
        "prompt_versions": sorted({str(c["prompt_version"]) for c in conversations}),
        "counts": {
            "window_traces": window_total,
            "langfuse_traces": len(records),
            "conversations": len(conversations),
            **selection,
        },
        "sample_seed": SAMPLE_SEED,
        "conversations": [
            {key: c[key] for key in ("id", "scenario_id", "session_id", "turn_count", "tools")}
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

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / f"{label}.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    flagged_random = sum(result["random_verdicts"].values())
    flagged_risk = sum(result["risk_verdicts"].values())
    print(f"  random flagged {flagged_random}/{len(result['random_verdicts'])}, risk flagged {flagged_risk}/{len(result['risk_verdicts'])}")
    print(f"  saved {out.relative_to(REPO_ROOT)}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--period", required=True, help="a period label from monitoring/config.json")
    parser.add_argument("--dry-run", action="store_true", help="fetch, validate, and sample without calling the judge")
    args = parser.parse_args(argv)

    from observability.instrument import load_env

    load_env()
    try:
        run_period(args.period, load_config(), dry_run=args.dry_run)
    except PeriodRejected as exc:
        print(f"period {args.period} rejected: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
