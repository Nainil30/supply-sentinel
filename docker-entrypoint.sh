#!/bin/sh
# Build the base snapshot on first start (seeded, deterministic), then run the chosen service.
set -e
if [ ! -f "$SENTINEL_DB" ]; then
  sentinel datagen --seed 42
fi
case "$1" in
  ui) exec sentinel ui --profile replay --hosted --port 8501 ;;
  mcp) exec sentinel serve-mcp --transport http --host 0.0.0.0 --port 8765 ;;
  *) exec sentinel "$@" ;;
esac
