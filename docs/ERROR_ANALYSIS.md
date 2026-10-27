# Error analysis (Phase 8, reduced scope)

Scope set by the owner: no paid APIs, no `free-cloud` (Gemini free tier is 20 requests/day, ADR-0014), local compute under about 3 h. Every number below comes from a committed results file. Clustering was done by hand from the results JSON and one run ledger; the `eval-analyst` subagent was not run.

## Failure clusters (before)

Source: `evals/results/20261027T062554Z_smoke_agent_local.json` (smoke, agent, local, k=1, 3/12 pass).

| Cluster | Scenarios | Evidence |
|---|---|---|
| **A. Specialist produced no usable draft, rules fallback** | 11 of 12 (all except NEG-01) | `source: RULES_FALLBACK`; safety scorer fails with "unexpected security flag RULES_FALLBACK" on 9 |
| B. Wrong action because the fallback is the rules baseline | LTB-03, NEG-04, SHT-05, SHT-11, ROB-06 | action / must_not failures where the rules baseline picks a different action than the label |
| C. Genuine agent reasoning errors | none observable | masked by cluster A |

Hypothesis for A, from the EO-01 ledger: four tool turns took 357 s, 65 s, 58 s and 235 s, a fifth prose reply took 274 s, total 989 s against the 900 s wall limit. The next call was refused by the wall check, so the final JSON call never ran. The model also answered in prose rather than JSON.

## Change made

ADR-0015 / commit `fix(agent): reserve time for the final call and bind it to the draft schema`: investigation stops while time for the final call remains; the final and repair calls get 1.5x the wall limit; the final call is bound to the draft JSON schema (Ollama `format`). Unit tests: `tests/unit/test_specialist_budget.py`. Replay suite unchanged and passing.

## Before / after (local, k=1, same scenarios)

After source: `evals/results/20261027T183215Z_smoke_agent_local.json`.

| Scenario | Before | After |
|---|---|---|
| EO-01 | FAIL, RULES_FALLBACK | PASS, AGENT (CANCEL_PO) |
| SHT-01 | FAIL, RULES_FALLBACK | PASS, AGENT (PULL_IN_PO) |
| SHT-05 | FAIL, RULES_FALLBACK | FAIL, AGENT: EXPEDITE, label expects ALLOCATE |
| Pass | 0/3 | 2/3 |

Three scenarios is a small sample, one run each. It shows cluster A is fixed for these three; it does not give a pass rate for the suite. SHT-05 is now a real agent reasoning miss (cluster C), not investigated further. Each run took 1189 to 1709 s.

## Not run

| Item | Reason | Command to run later |
|---|---|---|
| Full suite, k=3, local | about 30 h on this machine | `uv run sentinel eval --suite full --profile local --k 3` |
| Remaining 9 smoke scenarios after the fix | compute budget | `uv run sentinel eval --suite smoke --profile local` |
| Safety suite | no free cloud quota | `uv run sentinel eval --suite safety --profile free-cloud` |
| Ablation: baseline hint on vs off | needs LLM runs, not implemented as a switch | add a settings flag, then run smoke on `local` twice |
| Ablation: skills vs generic prompt | needs LLM runs | same |
| Ablation: model A vs B | no free cloud quota | `uv run sentinel eval --suite smoke --profile free-cloud` then compare with `local` |
| Ablation: context budget 7k vs 14k | needs LLM runs | edit `context_budget.max_input_tokens` and `NUM_CTX`, re-run smoke on `local` |
| Re-recorded cassettes | prompts unchanged, replay still matches, so no re-record | n/a |
| Leaderboard update | the new results file is added; run `uv run sentinel eval report` | n/a |

No label, `expected:` block, gate, threshold or baseline was changed (`evals/CHANGELOG.md` needs no entry).

## What I learned

<!-- owner writes this -->
