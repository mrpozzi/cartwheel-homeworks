"""Homework 6: the Harbor judge reads the input format the HW5 judge was tested on.

The frozen ``unsupported_assertion`` judge was developed and tested on the
records written by ``analysis/run_judges.prepare_inputs``: a ``context:`` line
with the session context the agent was given, the narration before each tool
call as its own ``assistant:`` line, ``tool_call`` and ``tool_result`` payloads
that carry the tool name, and the reply. The supplied ``judge_trace_text``
emits the default course format instead, so the generated Harbor judge uses
``hw5_judge_trace_text``. These checks run one scripted conversation through
the real Runner and assert that the Harbor text equals, byte for byte, what
the HW5 pipeline produces for the same session. No model call.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from agents import Agent
from agents.tracing import set_trace_provider
from agents.tracing.setup import get_trace_provider
from agents.tracing.provider import DefaultTraceProvider

from agent.agent import TOOLS_BY_ROLE, render_system_prompt
from agent.auth import AuthContext
from harbor_adapter.export import export_tasks
from replay.rollout import hw5_judge_trace_text, run_case
from tests.eval.fake_model import FakeModel, text_message, tool_call

NARRATION_1 = "Let me look up that order for you."
REPLY_1 = "Order 4127 was delivered, and it is still inside the return window."
NARRATION_2 = "Checking the store's return terms and the platform policy."
REPLY_2 = "Juniper's window is 14 days from delivery, per cw-returns."

CASE = {
    "id": "t-hw6-format",
    "mode": "unsupported_assertion",
    "input": {
        "role": "shopper",
        "user_id": 1,
        "message": "Can I still return order 4127?",
        "followups": ["What is the store's return window?"],
    },
    "initial_state": {"world": "reseed", "fixture": None, "assumes": "demo order 4127"},
    "expected": {"assertions": ["format check"], "judges": {"unsupported_assertion": "pass"}},
}


@pytest.fixture(autouse=True)
def disable_hosted_tracing() -> Iterator[None]:
    """Keep scripted offline runs from starting the hosted trace exporter."""
    previous_provider = get_trace_provider()
    offline_provider = DefaultTraceProvider()
    offline_provider.set_disabled(True)
    set_trace_provider(offline_provider)
    try:
        yield
    finally:
        set_trace_provider(previous_provider)
        offline_provider.shutdown()


def _scripted_run(world_copy: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, FakeModel]:
    """Play CASE through the real Runner with scripted model turns."""
    del world_copy  # the fixture points CARTWHEEL_DB at a private copy
    fake = FakeModel()
    # Turn 1: narration plus one tool call, then the reply.
    fake.set_next_output([text_message(NARRATION_1), tool_call("get_order", {"order_id": 4127})])
    fake.set_next_output([text_message(REPLY_1)])
    # Turn 2: narration plus two parallel calls in one generation, then the reply.
    fake.set_next_output(
        [
            text_message(NARRATION_2),
            tool_call("get_store_info", {"store_id": 1}),
            tool_call("get_policy", {"policy_id": "cw-returns"}),
        ]
    )
    fake.set_next_output([text_message(REPLY_2)])

    def fake_build_agent(ctx: AuthContext, model=None, **_: object) -> Agent[AuthContext]:
        return Agent[AuthContext](
            name="cartwheel-support",
            instructions=render_system_prompt(ctx),
            tools=TOOLS_BY_ROLE[ctx.role],
            model=fake,
        )

    monkeypatch.setattr("agent.agent.build_agent", fake_build_agent)
    return run_case(CASE), fake


def test_harbor_judge_text_equals_the_hw5_judge_input(world_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # analysis.run_judges points the helpers at the HW5 inputs on import; keep
    # that out of the other tests' environment.
    monkeypatch.delenv("CARTWHEEL_JUDGE_TRACE_SOURCE", raising=False)
    from analysis.helpers.normalization import _flatten
    from analysis.review_app.loader import _context_lines
    from analysis.run_judges import _turn_messages

    transcript, fake = _scripted_run(world_copy, monkeypatch)
    turn_1, turn_2 = transcript["turns"]
    results_1 = [call["result"] for call in turn_1["tool_calls"]]
    results_2 = [call["result"] for call in turn_2["tool_calls"]]
    assert all(isinstance(result, dict) for result in results_1 + results_2)

    # The HW5 side, built from the script and the instructor's transcript
    # fields only: the HW4 review interface's step grouping, then the HW5
    # message layout, then the helpers' flattening.
    traced_system_prompt = fake.requests[0]["system_instructions"]
    hw4_turns = [
        {
            "user": CASE["input"]["message"],
            "steps": [
                {
                    "narration": NARRATION_1,
                    "tool_call": {"name": "get_order", "arguments": {"order_id": 4127}},
                    "tool_result": {"output": results_1[0]},
                }
            ],
            "reply": REPLY_1,
        },
        {
            "user": CASE["input"]["followups"][0],
            "steps": [
                {
                    "narration": NARRATION_2,
                    "tool_call": {"name": "get_store_info", "arguments": {"store_id": 1}},
                    "tool_result": {"output": results_2[0]},
                },
                {
                    "narration": "",
                    "tool_call": {"name": "get_policy", "arguments": {"policy_id": "cw-returns"}},
                    "tool_result": {"output": results_2[1]},
                },
            ],
            "reply": REPLY_2,
        },
    ]
    messages = [
        {
            "role": "context",
            "text": "Session context given to the agent: " + "; ".join(_context_lines(traced_system_prompt)),
        }
    ]
    for turn in hw4_turns:
        messages.extend(_turn_messages(turn))
    expected = _flatten(messages)

    actual = hw5_judge_trace_text(transcript)
    assert actual == expected

    lines = actual.splitlines()
    assert lines[0] == (
        "context: Session context given to the agent: User role: shopper; "
        "User id: 1; Store id: none; Today's date: 2026-07-01"
    )
    assert lines[1] == f"user: {CASE['input']['message']}"
    assert lines[2] == f"assistant: {NARRATION_1}"
    assert lines[3].startswith('tool_call: {"arguments": {"order_id": 4127}, "tool": "get_order"}')
    assert lines[4].startswith('tool_result: {"result": {') and lines[4].endswith('"tool": "get_order"}')
    assert lines[5] == f"assistant: {REPLY_1}"
    # The parallel second call has no narration of its own.
    assert [line.split(":", 1)[0] for line in lines[6:]] == [
        "user", "assistant", "tool_call", "tool_result", "tool_call", "tool_result", "assistant"
    ]

    # The instructor's transcript fields are unchanged for the checks engine.
    assert turn_1["reply"] == f"{NARRATION_1}\n{REPLY_1}"
    assert transcript["final_reply"] == f"{NARRATION_2}\n{REPLY_2}"
    assert [call["name"] for call in turn_2["tool_calls"]] == ["get_store_info", "get_policy"]


def _write_frozen_judge(state: Path) -> None:
    judges = state / "judges"
    judges.mkdir(parents=True)
    (judges / "unsupported_assertion-v2.json").write_text(
        json.dumps(
            {
                "judge_id": "unsupported_assertion-v2",
                "mode": "unsupported_assertion",
                "version": 2,
                "prompt_text": "Judge whether the reply invents a value.",
                "model": "gpt-4o-mini",
                "status": "frozen",
            }
        )
    )


def test_generated_judge_uses_the_hw5_format_and_the_hw5_parser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    _write_frozen_judge(state)
    monkeypatch.setenv("CARTWHEEL_ANALYSIS_STATE", str(state))
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(json.dumps(CASE) + "\n")

    export_tasks(cases_path, tmp_path / "tasks", baseline=True, require_suite=False)
    rubric_path = tmp_path / "tasks" / CASE["id"] / "tests" / "judge_unsupported_assertion.py"
    rubric = rubric_path.read_text()
    assert "hw5_judge_trace_text(evidence[\"transcript\"])" in rubric
    assert "MODEL = 'gpt-4o-mini'" in rubric

    # Run the generated parser on its own: the HW5 helpers accept exactly
    # Pass or Fail with a critique and raise on anything else.
    module = ast.parse(rubric)
    decode_source = next(
        ast.get_source_segment(rubric, node)
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_decode"
    )
    namespace: dict[str, object] = {}
    exec(decode_source, namespace)  # noqa: S102 - the generated file is our own
    decode = namespace["_decode"]
    assert decode({"result": "Pass", "critique": "every value is in a tool result"}) == "pass"
    assert decode({"result": "Fail", "critique": "the deadline is invented"}) == "fail"
    for bad in ({"result": "passed", "critique": "x"}, {"result": "Not found", "critique": "x"}, {"result": "Pass"}):
        with pytest.raises(ValueError):
            decode(bad)
