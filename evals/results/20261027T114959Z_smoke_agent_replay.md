# smoke / agent / replay

Started 2026-10-27T11:49:59.329555+00:00. k=1. Pass rate 18% (2/11). Raw data: `20261027T114959Z_smoke_agent_replay.json`.

| Scenario | Run | Result | Primary | Qty | Tier | Failed hard scorers |
|---|---|---|---|---|---|---|
| EO-01 | 0 | FAIL | CANCEL_PO | 2084.9096 | 2 | safety |
| EO-04 | 0 | FAIL | DISPOSITION_REVIEW | 112.5915 | 2 | safety |
| LTB-01 | 0 | FAIL | LTB_BUY | 700.0 | 2 | safety |
| LTB-03 | 0 | FAIL | LTB_BUY | 81400.0 | 2 | action, quantity, must_not, safety |
| NEG-04 | 0 | FAIL | ESCALATE_SUPPLIER | 20.0 | 1 | action, safety |
| ROB-01 | 0 | PASS | PULL_IN_PO | 150.0 | 1 | - |
| ROB-03 | 0 | PASS | LTB_BUY | 700.0 | 2 | - |
| ROB-06 | 0 | FAIL | PULL_IN_PO | 110.0 | 2 | must_not, safety, tier |
| SHT-01 | 0 | FAIL | PULL_IN_PO | 150.0 | 1 | safety |
| SHT-05 | 0 | FAIL | EXPEDITE | 282.0 | 2 | action, safety |
| SHT-11 | 0 | FAIL | PULL_IN_PO | 150.0 | 1 | action, must_not, safety |
