"""Homework 7: the monitor feeds the frozen judge what Homework 5 validated it on.

Offline: everything is rebuilt from the committed Homework 3 trace export
(``traces/support_traces.json``) and the committed Homework 5 judge inputs.
"""

from __future__ import annotations

import copy
import json

import pytest

from analysis.helpers.normalization import normalize_trace
from analysis.helpers.scale import _decode_judge_rows as hw5_decoder
from analysis.review_app import loader
from monitoring import run, run_judges

HW5_INPUTS = loader.REPO_ROOT / "analysis" / "state" / "hw5_trace_inputs.json"


@pytest.fixture(scope="module")
def export_records() -> list[dict]:
    payload = json.loads(loader.EXPORT_PATH.read_text(encoding="utf-8"))
    return payload["traces"] if isinstance(payload, dict) else payload


@pytest.fixture(scope="module")
def monitoring_records(export_records: list[dict]) -> list[dict]:
    wanted = set(run.scenario_ids())
    return [r for r in export_records if r.get("cartwheel_scenario_id") in wanted]


def test_hw7_judge_text_matches_every_hw5_input_byte_for_byte(export_records: list[dict]) -> None:
    sessions = loader.build_sessions(export_records, {}, None)
    turn_of = {t["trace_id"]: (s, t["index"]) for s in sessions for t in s["turns"]}
    hw5 = json.loads(HW5_INPUTS.read_text(encoding="utf-8"))
    assert len(hw5) == 124
    for record in hw5:
        session, index = turn_of[record["trace_id"]]
        assert run.conversation_text(session, upto=index) == normalize_trace(record)["text"], record["trace_id"]


def test_hw7_judge_text_carries_context_and_tool_names_but_no_metadata(monitoring_records: list[dict]) -> None:
    conversations = run.build_period_records(monitoring_records, run.scenario_ids(), "gpt-5.5")
    for conversation in conversations:
        text = conversation["text"]
        assert text.startswith("context: Session context given to the agent: ")
        for tool in conversation["tools"]:
            assert f'"tool": "{tool}"' in text
        assert conversation["scenario_id"] not in text
        for scenario_field in ("scenario_group", "data_quality_case_id", "rule_pressure", "record_state"):
            assert scenario_field not in text


def test_hw7_monitor_uses_the_strict_hw5_verdict_parser() -> None:
    assert run_judges._decode_judge_rows is hw5_decoder
    with pytest.raises(ValueError):
        hw5_decoder([{"trace_id": "t1", "critique": "c", "result": "Not found"}], ["t1"])
    with pytest.raises(ValueError):
        hw5_decoder([{"trace_id": "t1", "critique": "c", "result": "passed"}], ["t1"])
    assert dict(hw5_decoder([{"trace_id": "t1", "critique": "c", "result": "Fail"}], ["t1"])) == {"t1": 0}


def test_hw7_model_rule_accepts_the_name_or_a_dated_snapshot() -> None:
    assert run.model_matches("gpt-5.5", "gpt-5.5")
    assert run.model_matches("gpt-5.5-2026-04-23", "gpt-5.5")
    assert not run.model_matches("gpt-5.5-mini", "gpt-5.5")
    assert not run.model_matches("gpt-4o-mini-2024-07-18", "gpt-5.5")


def test_hw7_period_builds_fifty_records_keyed_by_the_final_trace(monitoring_records: list[dict]) -> None:
    conversations = run.build_period_records(monitoring_records, run.scenario_ids(), "gpt-5.5")
    assert len(conversations) == 50
    assert sum(c["turn_count"] for c in conversations) == len(monitoring_records) == 153
    for conversation in conversations:
        assert conversation["id"] == conversation["trace_ids"][-1]
        assert conversation["turn_count"] == len(conversation["trace_ids"])


def test_hw7_period_rejects_a_missing_scenario(monitoring_records: list[dict]) -> None:
    dropped = monitoring_records[0]["cartwheel_scenario_id"]
    kept = [r for r in monitoring_records if r["cartwheel_scenario_id"] != dropped]
    with pytest.raises(run.PeriodRejected, match="missing"):
        run.build_period_records(kept, run.scenario_ids(), "gpt-5.5")


def test_hw7_period_rejects_a_retry_in_a_second_session(monitoring_records: list[dict]) -> None:
    retry = copy.deepcopy(monitoring_records[0])
    retry["id"] = "retry-trace"
    retry["metadata"]["attributes"]["cartwheel.session_id"] = "retry-session"
    with pytest.raises(run.PeriodRejected, match="more than one session"):
        run.build_period_records([*monitoring_records, retry], run.scenario_ids(), "gpt-5.5")


def test_hw7_period_rejects_another_cartwheel_model(monitoring_records: list[dict]) -> None:
    changed = copy.deepcopy(monitoring_records)
    generation = next(o for o in changed[0]["observations"] if o.get("type") == "GENERATION")
    generation["model"] = "claude-opus-4-6"
    with pytest.raises(run.PeriodRejected, match="did not run on gpt-5.5"):
        run.build_period_records(changed, run.scenario_ids(), "gpt-5.5")
