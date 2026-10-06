# Homework 7 monitoring

`unsupported_assertion` monitored with the frozen judge `unsupported_assertion-v2` (gpt-4o-mini) on Cartwheel `gpt-5.5`, 50 scenarios per period (before: the HW3 run, 2026-09-23 14:19–15:50 UTC; after: a new run, 2026-10-03 17:38–18:02 UTC), random rate 0.2, risk group `multi_turn`, threshold 0.15.

**1. Did the corrected failure estimate move between the two periods?**

The failure estimate looks basically the same (due to the fact that we ran the same prompt).

**2. Do the intervals support a conclusion, or is the result uncertain?**

CI's are way too large to conclude anything.

**3. What did the risk groups reveal that the random estimate did not?**

Here too the behavior is unchanged, but focusing on risk groups increases the sample size, which is useful: it gives more conversations to inspect (38 instead of 10). It does not make the failure-rate estimate more precise, because the risk group is not a random sample; only the random sample estimates the rate.

**4. What action should happen if the estimate crosses the threshold?**

Crossing the threshold is a warning and an indication of the need to get back to the drawing board to do more error analysis.
