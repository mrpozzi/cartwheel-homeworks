"""Bias-corrected failure prevalence for a monitoring period."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np


def corrected_mode_prevalence(
    sample_preds: Sequence[int],
    test_labels: Sequence[int],
    test_preds: Sequence[int],
    confidence: float = 0.95,
    bootstrap_iterations: int = 20000,
    seed: int | None = 7,
) -> dict[str, Any]:
    """Bias-corrected live prevalence for one mode from sampled verdicts.

    The contract, precisely:

      1. ``raw`` is the uncorrected flag rate: ``mean(sample_preds)``.
      2. Compute the frozen judge's failure sensitivity and pass specificity
         from ``test_labels`` and ``test_preds``. Both use the monitoring
         convention that 1 means a failure is present. Failure sensitivity is
         the flagged fraction of human-labeled failures. Pass specificity is
         the unflagged fraction of human-labeled passes.
      3. Compute the Rogan-Gladen point estimate, then resample the held-out
         records and sampled predictions to obtain a percentile-bootstrap
         interval. Use a seeded NumPy generator so the committed result is
         reproducible.
      4. Resample the monitoring predictions and the paired held-out records
         independently with replacement. Keep their original sample sizes.
         Discard a draw if the correction cannot be computed. Clamp each
         retained estimate to [0, 1], then take the percentile interval.
         Raise ``ValueError`` if no replicate is valid.

    Args:
        sample_preds: the judge's 0/1 verdicts over the UNIFORM BASE sample
            only (never the risk strata; they are biased toward failure by
            design).
        test_labels: human labels for the frozen Homework 5 judge's test
            split.
        test_preds: the frozen judge's predictions on that test split.
        confidence: interval confidence level.
        bootstrap_iterations: number of percentile-bootstrap replicates.
        seed: numpy seed for a reproducible interval; None leaves the RNG
            untouched.

    Returns:
        {"raw", "corrected", "ci_low", "ci_high", "confidence",
         "failure_sensitivity", "pass_specificity", "n_sample"}
        with "corrected" clamped to [0, 1] and rates rounded to 4 places.

    Raises:
        ValueError: if an input is empty, the held-out inputs have different
            lengths, a value is not 0 or 1, a class is absent, the judge is
            missing a usable correction, or no bootstrap replicate is valid.
    """
    if not sample_preds or not test_labels or not test_preds:
        raise ValueError("sample predictions and held-out records must be nonempty")
    if len(test_labels) != len(test_preds):
        raise ValueError("held-out labels and predictions differ in length")
    if any(v not in (0, 1) for v in [*sample_preds, *test_labels, *test_preds]):
        raise ValueError("every label and prediction must be 0 or 1")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be in (0, 1)")
    if bootstrap_iterations < 1:
        raise ValueError("bootstrap_iterations must be positive")

    sample = np.asarray(sample_preds, dtype=float)
    labels = np.asarray(test_labels, dtype=float)
    preds = np.asarray(test_preds, dtype=float)
    if labels.sum() == 0 or labels.sum() == len(labels):
        raise ValueError("the held-out records need both human failures and human passes")

    raw = float(sample.mean())
    sensitivity = float((labels * preds).sum() / labels.sum())
    specificity = float(((1 - labels) * (1 - preds)).sum() / (1 - labels).sum())
    youden = sensitivity + specificity - 1
    if youden <= 0:
        raise ValueError("the judge is no better than chance on the held-out records; no usable correction")
    corrected = min(1.0, max(0.0, (raw + specificity - 1) / youden))

    # Two independent sources of uncertainty: which conversations were sampled,
    # and how well the held-out records pin down the judge's error rates.
    rng = np.random.default_rng(seed)
    raw_b = sample[rng.integers(0, len(sample), size=(bootstrap_iterations, len(sample)))].mean(axis=1)
    rows = rng.integers(0, len(labels), size=(bootstrap_iterations, len(labels)))
    labels_b, preds_b = labels[rows], preds[rows]
    failures_b = labels_b.sum(axis=1)
    passes_b = len(labels) - failures_b
    with np.errstate(divide="ignore", invalid="ignore"):
        sensitivity_b = (labels_b * preds_b).sum(axis=1) / failures_b
        specificity_b = ((1 - labels_b) * (1 - preds_b)).sum(axis=1) / passes_b
        youden_b = sensitivity_b + specificity_b - 1
        estimates = (raw_b + specificity_b - 1) / youden_b
    valid = (failures_b > 0) & (passes_b > 0) & (youden_b > 0)
    if not valid.any():
        raise ValueError("no bootstrap replicate produced a usable correction")
    estimates = np.clip(estimates[valid], 0.0, 1.0)
    tail = (1 - confidence) / 2 * 100
    ci_low, ci_high = np.percentile(estimates, [tail, 100 - tail])

    return {
        "raw": round(raw, 4),
        "corrected": round(corrected, 4),
        "ci_low": round(float(ci_low), 4),
        "ci_high": round(float(ci_high), 4),
        "confidence": confidence,
        "failure_sensitivity": round(sensitivity, 4),
        "pass_specificity": round(specificity, 4),
        "n_sample": len(sample_preds),
    }
