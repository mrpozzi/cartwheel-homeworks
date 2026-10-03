"""Homework 6: read Harbor 0.23.0 jobs, whose trial results live per trial.

Harbor 0.23.0 writes the job-level ``result.json`` with ``trial_results``
excluded and keeps one ``result.json`` per trial directory. The summary and
the trial-count analysis read those files, ordered by the time Harbor started
each trial, and still accept a job file that embeds ``trial_results``.
"""

from __future__ import annotations

import json
from pathlib import Path

from harbor_adapter.analysis import analyze_capability_job
from harbor_adapter.summary import load_trial_results, summarize_job

CASE = {
    "id": "e-777",
    "mode": "policy_claim_without_citation",
    "kind": "capability",
    "baseline_pass_rate": 0.4,
    "input": {"role": "shopper", "user_id": 1, "message": "Can I return this?"},
    "initial_state": {"world": "reseed", "fixture": None},
    "expected": {
        "assertions": ["The reply cites the policy."],
        "checks": [{"check": "reply_contains", "text": "cw-returns"}],
    },
}


def _write_harbor_job(job_dir: Path, rewards: list[float | None], *, errors: set[int] = frozenset()) -> None:
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text(
        json.dumps({"id": "job", "started_at": "2026-09-30T12:00:00", "n_total_trials": len(rewards), "stats": {}})
    )
    for index, reward in enumerate(rewards):
        # Directory names sort differently from start times on purpose.
        name = f"e-777__{'zyxwvutsrqponmlkjihgfedcba'[index]}"
        trial_dir = job_dir / name
        trial_dir.mkdir()
        trial = {
            "task_name": "cartwheel/evals__e-777",
            "trial_name": name,
            "started_at": f"2026-09-30T12:{index:02d}:00",
            "agent_info": {"name": "cartwheel", "version": "1.0.0", "model_info": {"name": "gpt-5.5", "provider": None}},
            "verifier_result": None if reward is None else {"rewards": {"reward": reward}},
            "exception_info": {"exception_type": "VerifierTimeoutError"} if index in errors else None,
        }
        (trial_dir / "result.json").write_text(json.dumps(trial))


def test_trial_results_come_from_the_trial_directories_in_start_order(tmp_path: Path) -> None:
    job = tmp_path / "job"
    _write_harbor_job(job, [1.0, 0.0, 1.0, 0.0, 0.0])
    trials = load_trial_results(job)
    assert [t["started_at"][-5:] for t in trials] == ["00:00", "01:00", "02:00", "03:00", "04:00"]
    assert [t["verifier_result"]["rewards"]["reward"] for t in trials] == [1.0, 0.0, 1.0, 0.0, 0.0]


def test_embedded_trial_results_are_still_used(tmp_path: Path) -> None:
    job = tmp_path / "job"
    job.mkdir()
    embedded = [{"task_name": "cartwheel/evals__e-777", "trial_name": "e-777__a"}]
    (job / "result.json").write_text(json.dumps({"trial_results": embedded}))
    assert load_trial_results(job) == embedded


def test_summary_and_analysis_read_the_per_trial_layout(tmp_path: Path, monkeypatch) -> None:
    # Stub the Part B functions like the instructor's adapter test does, so
    # this layout check does not depend on them.
    import tests.eval.passk as passk

    monkeypatch.setattr(passk, "pass_at_k", lambda n, c, k: c / n)
    monkeypatch.setattr(passk, "pass_hat_k", lambda n, c, k: (c / n) ** k)
    monkeypatch.setattr(
        passk, "case_passes", lambda kind, passes, n, rate=None: {"decision": "pass", "reason": "stub"}
    )
    monkeypatch.setattr("harbor_adapter.analysis.pass_at_k", lambda n, c, k: c / n)
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(json.dumps(CASE) + "\n")
    job = tmp_path / "hw6-evals"
    _write_harbor_job(job, [1.0, 0.0, 1.0, 0.0, 0.0])

    markdown, passed = summarize_job(job, cases_path=cases_path, expected_attempts=5)
    assert passed is True
    assert "| `e-777` | capability | 2 | 5 |" in markdown

    fifteen = tmp_path / "hw6-capability-15"
    _write_harbor_job(fifteen, [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0])
    analysis = analyze_capability_job(fifteen, "e-777")
    assert analysis["rewards"] == [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 0, 0]
    assert analysis["model"] == "gpt-5.5"
    assert analysis["trial_order"] == "per-trial result.json files sorted by started_at"
    assert [c["successes"] for c in analysis["comparisons"]] == [1, 2, 3]


def test_baseline_summary_reports_a_timed_out_trial_as_infrastructure(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.jsonl"
    unclassified = {k: v for k, v in CASE.items() if k not in ("kind", "baseline_pass_rate")}
    cases_path.write_text(json.dumps(unclassified) + "\n")
    job = tmp_path / "hw6-baseline-e-777"
    _write_harbor_job(job, [0.0, 0.0, None, None, None], errors={2, 3, 4})

    markdown, passed = summarize_job(job, cases_path=cases_path, expected_attempts=5, classify=True)
    assert passed is False
    assert "do not classify; complete five valid trials" in markdown
    assert "e-777: 3 trial(s) did not produce a reward" in markdown
