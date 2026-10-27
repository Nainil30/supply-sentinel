# Supply Sentinel

Agentic supply-execution copilot on synthetic data for a fictional OEM. Work in progress.

## Commands available so far

```bash
uv sync
uv run sentinel datagen --seed 42 --summary data/sample/base_summary.md   # build data/snapshots/base.duckdb
uv run sentinel datagen --check-determinism                              # build twice, compare table hashes
uv run sentinel eval --suite smoke --profile replay                    # score the agent on recorded cassettes
uv run sentinel eval --system rules --suite full                       # rules baseline on all 40 scenarios
uv run sentinel eval report                                            # evals/results/leaderboard.md
uv run sentinel eval calibrate                                         # judge vs reference labels
uv run sentinel scenario build <id>                                      # apply evals/scenarios/<id>.yaml perturbations
uv run sentinel detect                                                           # exceptions + rules-baseline action for a snapshot (no LLM)
uv run python scripts/check_worked_examples.py                           # stdlib recompute of engine worked examples A-C
uv run pytest -m "not live"
```

The generated data is described in [`data/sample/base_summary.md`](data/sample/base_summary.md).
