# Judge calibration

> **Provisional - AI reference labels, owner relabel pending.** These numbers say how well the judge matches a second model working from the rubric alone, not how well it matches a planner.

Judge model: `gemma4:e4b`. Labels: `ai-reference`, 15 messages.

| Criterion | Agreement |
|---|---|
| bottom_line_first | 80% |
| right_audience | 67% |
| numbers_consistent | 87% |
| decision_and_deadline | 100% |
| concise_professional | 100% |

Pass/fail agreement: 93%. Cohen's kappa: **0.86** (meets the 0.6 target).
