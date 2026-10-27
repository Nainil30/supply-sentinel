# Supply Sentinel

Agentic supply-execution copilot on synthetic data for a fictional OEM. Work in progress.

## Commands available so far

```bash
uv sync
uv run sentinel datagen --seed 42 --summary data/sample/base_summary.md   # build data/snapshots/base.duckdb
uv run sentinel datagen --check-determinism                              # build twice, compare table hashes
uv run sentinel scenario build <id>                                      # apply evals/scenarios/<id>.yaml perturbations
uv run pytest -m "not live"
```

The generated data is described in [`data/sample/base_summary.md`](data/sample/base_summary.md).
