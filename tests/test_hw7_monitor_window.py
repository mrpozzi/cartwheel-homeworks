"""Homework 7: the scheduled ``--last-hours`` mode, offline.

Built from the committed Homework 3 export; Langfuse and the judge are
replaced so nothing leaves the machine.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from analysis.review_app import loader
from monitoring import run

@pytest.fixture(scope="module")
def export_records() -> list[dict]:
    payload = json.loads(loader.EXPORT_PATH.read_text(encoding="utf-8"))
    return payload["traces"] if isinstance(payload, dict) else payload


@pytest.fixture(scope="module")
def window(export_records: list[dict]) -> tuple[datetime, datetime]:
    """A 20-minute window that opens in the middle of a multi-turn conversation."""
    sessions = loader.build_sessions(export_records, {}, None)
    straddling = next(s for s in sessions if s["turn_count"] >= 3)
    start = run._parse_utc(straddling["turns"][1]["timestamp"])
    return start, start + timedelta(minutes=20)


def test_hw7_window_keeps_conversations_whose_final_trace_is_inside(
    export_records: list[dict], window: tuple[datetime, datetime]
) -> None:
    START, END = window
    sessions = loader.build_sessions(export_records, {}, None)
    ending_inside = {
        s["session_id"] for s in sessions if START <= run._parse_utc(s["turns"][-1]["timestamp"]) < END
    }
    started_before = {
        s["session_id"] for s in sessions
        if s["session_id"] in ending_inside and run._parse_utc(s["turns"][0]["timestamp"]) < START
    }
    assert ending_inside and started_before, "the window should exercise the lookback"

    conversations, skipped = run.build_window_records(export_records, START, END, "gpt-5.5")
    assert skipped == {}
    assert {c["session_id"] for c in conversations} == ending_inside
    by_session = {s["session_id"]: s for s in sessions}
    for conversation in conversations:
        # Earlier turns from before the window stay in the conversation.
        assert conversation["trace_ids"] == by_session[conversation["session_id"]]["trace_ids"]
        assert conversation["id"] == conversation["trace_ids"][-1]
    assert [c["timestamp"] for c in conversations] == sorted(c["timestamp"] for c in conversations)


def test_hw7_window_skips_and_counts_another_cartwheel_model(
    export_records: list[dict], window: tuple[datetime, datetime]
) -> None:
    START, END = window
    conversations, _ = run.build_window_records(export_records, START, END, "gpt-5.5")
    target = conversations[0]["trace_ids"][-1]
    changed = copy.deepcopy(export_records)
    record = next(r for r in changed if r["id"] == target)
    next(o for o in record["observations"] if o.get("type") == "GENERATION")["model"] = "claude-opus-4-6"

    kept, skipped = run.build_window_records(changed, START, END, "gpt-5.5")
    assert len(kept) == len(conversations) - 1
    assert target not in {c["id"] for c in kept}
    assert sum(skipped.values()) == 1 and any("claude-opus-4-6" in key for key in skipped)


def test_hw7_empty_window_records_zero_and_calls_no_judge(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    history = tmp_path / "history.jsonl"
    history.write_text(run.HISTORY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(run, "HISTORY_PATH", history)
    monkeypatch.setattr(run, "CHART_PATH", tmp_path / "prevalence.svg")
    monkeypatch.setattr(run, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(run, "fetch_window", lambda start, end, keep: ([], 0))

    def forbidden(*args, **kwargs):
        raise AssertionError("an empty window must not judge or write scores")

    import monitoring.run_judges as run_judges

    monkeypatch.setattr(run_judges, "judge_sample", forbidden)
    monkeypatch.setattr(run, "write_monitor_trace", forbidden)
    monkeypatch.setattr(run, "verify_scores", forbidden)

    result = run.run_window(24, run.load_config(), now=datetime(2026, 10, 6, 6, 17, tzinfo=timezone.utc))
    assert result["counts"]["conversations"] == 0 and result["counts"]["judge_calls"] == 0
    lines = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()]
    assert [h["period"] for h in lines] == ["before", "after", "daily-2026-10-06"]
    assert lines[-1]["conversations"] == 0 and lines[-1]["corrected"] is None
    assert (tmp_path / "output" / "daily-2026-10-06.json").exists()
    assert (tmp_path / "prevalence.svg").read_text(encoding="utf-8").count("<circle") == 2


def test_hw7_cli_takes_a_period_or_positive_last_hours() -> None:
    with pytest.raises(SystemExit):
        run.main(["--period", "after", "--last-hours", "24"])
    with pytest.raises(SystemExit):
        run.main(["--last-hours", "0"])
