#!/usr/bin/env bash
# Supply Sentinel autopilot.
# Runs build phases 0-9 with Claude Code in headless mode, one fresh session per phase.
# For each phase: branch -> build -> phase gate -> (fix loop) -> push -> PR -> wait for CI
# (fix loop) -> merge -> tag. Resumable: rerun the script and it continues where it stopped.
#
# Usage (from the repo root):
#   ./scripts/autopilot.sh                      # run everything
#   ONLY_PHASE=3 ./scripts/autopilot.sh         # run a single phase
#   PERM_MODE=acceptEdits ./scripts/autopilot.sh  # if auto mode is unavailable on your plan
#   MODEL=sonnet ./scripts/autopilot.sh         # pick the Claude model for build sessions
#
# Requires: claude (logged in), uv, git, gh (authenticated), python3.
# On macOS, keep the laptop awake: caffeinate -i ./scripts/autopilot.sh

set -euo pipefail

PHASES=(00-bootstrap 01-data 02-engine 03-mcp-server 04-agent-core 05-specialists 06-evals 07-observability-ui 08-error-analysis 09-ship)
PERM_MODE=${PERM_MODE:-auto}
MAX_FIX_ATTEMPTS=${MAX_FIX_ATTEMPTS:-2}
ONLY_PHASE=${ONLY_PHASE:-}
MODEL=${MODEL:-}
STATE_DIR=.autopilot
LOG_DIR=_private/autopilot

log() { printf '\n[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die() { log "STOPPED: $*"; log "Fix the issue, then rerun ./scripts/autopilot.sh — it resumes from this phase."; exit 1; }

json_field() {
  python3 - "$1" "$2" <<'PY' 2>/dev/null || true
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    value = data.get(sys.argv[2], "")
    print(value if isinstance(value, str) else json.dumps(value))
except Exception:
    print("")
PY
}

phase_status() {
  json_field "$1" result | grep -o 'PHASE_STATUS: .*' | tail -1 | sed 's/^PHASE_STATUS: //' || true
}

run_claude() { # $1 output json file, $2 prompt, $3 optional session id to resume
  local out=$1 prompt=$2 resume=${3:-}
  local args=(-p "$prompt" --permission-mode "$PERM_MODE" --permission-prompts none --output-format json)
  if [[ -n "$resume" ]]; then args+=(--resume "$resume"); fi
  if [[ -n "$MODEL" ]]; then args+=(--model "$MODEL"); fi
  SENTINEL_AUTOPILOT=1 claude "${args[@]}" >"$out" 2>"${out%.json}.stderr" || true
  local cost; cost=$(json_field "$out" total_cost_usd)
  log "Session finished (log: $out${cost:+, est. cost \$$cost})"
}

preflight() {
  [[ -f AUTOPILOT.md ]] || die "run this from the repo root (AUTOPILOT.md not found)"
  for c in claude uv git gh python3; do command -v "$c" >/dev/null || die "missing command: $c"; done
  gh auth status >/dev/null 2>&1 || die "GitHub CLI not logged in: run gh auth login"
  if ! grep -qE '^GITHUB_TOKEN=.+' .env 2>/dev/null && ! command -v ollama >/dev/null; then
    die "no model access for live evals: add GITHUB_TOKEN (Models: read) to .env, or install Ollama"
  fi
  command -v ollama >/dev/null || log "note: Ollama not installed; evals will use the GitHub Models free tier and may span several days of quota"
  command -v node >/dev/null || log "note: Node not installed; the MCP Inspector step in Phase 3 will be skipped"
  command -v docker >/dev/null || log "note: Docker not installed; the compose check in Phase 9 will be skipped"
  [[ -z "$(git status --porcelain)" ]] || die "working tree has uncommitted changes; commit or stash them first"
  mkdir -p "$STATE_DIR" "$LOG_DIR"
}

lock_baseline() {
  local f
  f=$(ls -t evals/results/*_smoke_agent_*.json 2>/dev/null | head -1 || true)
  if [[ -n "$f" && ! -f evals/results/baseline_main.json ]]; then
    cp "$f" evals/results/baseline_main.json
    git add evals/results/baseline_main.json
    git commit -qm "test(evals): lock smoke baseline from $(basename "$f")"
    log "Baseline locked from $f"
  elif [[ -z "$f" ]]; then
    log "note: no smoke results found; baseline not locked (see OWNER_REVIEW)"
  fi
}

wait_ci() { # $1 pr number, $2 phase number, $3 branch
  local pr=$1 num=$2 branch=$3 attempt=0
  sleep 30
  while true; do
    if gh pr checks "$pr" --watch --fail-fast >/dev/null 2>&1; then
      log "CI passed on PR #$pr"; return 0
    fi
    if gh pr checks "$pr" 2>&1 | grep -qi "no checks"; then
      log "note: no CI checks reported on PR #$pr; continuing"; return 0
    fi
    attempt=$((attempt + 1))
    (( attempt > MAX_FIX_ATTEMPTS )) && die "CI still failing on PR #$pr after $MAX_FIX_ATTEMPTS fix attempts"
    local run_id ci_log="$LOG_DIR/phase-$num-ci$attempt.log"
    run_id=$(gh run list --branch "$branch" --limit 1 --json databaseId -q '.[0].databaseId' 2>/dev/null || true)
    if [[ -n "$run_id" ]]; then gh run view "$run_id" --log-failed 2>/dev/null | tail -200 >"$ci_log" || true; fi
    log "CI failed on PR #$pr; fix attempt $attempt"
    git checkout -q "$branch"
    run_claude "$LOG_DIR/phase-$num-cifix$attempt.json" \
      "AUTOPILOT. CI failed on the Phase $num pull request. The failing log tail is in $ci_log. Reproduce it locally, fix it, commit, and end with the final status line."
    git push -q origin "$branch"
    sleep 30
  done
}

ship_phase() { # $1 branch, $2 phase number
  local branch=$1 num=$2 pr tag
  git push -q -u origin "$branch"
  pr=$(gh pr view "$branch" --json number -q .number 2>/dev/null || true)
  if [[ -z "$pr" ]]; then
    gh pr create --base main --head "$branch" \
      --title "Phase $num: ${branch#phase/}" \
      --body "Autopilot build of Phase $num (see docs/BUILD_PLAN.md). Review notes are kept privately by the owner." >/dev/null
    pr=$(gh pr view "$branch" --json number -q .number)
  fi
  wait_ci "$pr" "$num" "$branch"
  gh pr merge "$pr" --merge --delete-branch >/dev/null
  git checkout -q main
  git pull -q --ff-only
  if (( num == 9 )); then tag=v1.0.0; else tag="v0.$num.0"; fi
  if ! git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
    git tag "$tag"
    git push -q origin "$tag"
  fi
  log "Phase $num merged (PR #$pr) and tagged $tag"
}

run_phase() { # $1 phase id like 03-mcp-server
  local id=$1 n=${1%%-*} num branch out status sid attempt resume_note=""
  num=$((10#$n))
  branch="phase/$id"

  if [[ -f "$STATE_DIR/$id.done" ]]; then log "Phase $n already done; skipping"; return 0; fi

  git checkout -q main
  git pull -q --ff-only || true
  if git show-ref --verify --quiet "refs/heads/$branch"; then
    git checkout -q "$branch"
    resume_note="This branch already has work from an earlier attempt: read git log and _private/phase-plans/phase-$n.md, then continue from there."
  else
    git checkout -q -b "$branch"
  fi

  log "Phase $n ($id): building with Claude Code"
  out="$LOG_DIR/phase-$n-build.json"
  run_claude "$out" "AUTOPILOT. Read AUTOPILOT.md, CLAUDE.md and AGENTS.md, then execute Phase $num of docs/BUILD_PLAN.md end to end under the autopilot rules. $resume_note Finish with the phase-gate skill and the final status line."
  status=$(phase_status "$out")
  sid=$(json_field "$out" session_id)

  attempt=0
  while [[ "$status" != DONE* ]] && (( attempt < MAX_FIX_ATTEMPTS )); do
    attempt=$((attempt + 1))
    log "Phase $n not done (${status:-no status line}); fix attempt $attempt"
    out="$LOG_DIR/phase-$n-fix$attempt.json"
    run_claude "$out" "AUTOPILOT. Phase $num is not done yet: '${status:-no status line was printed}'. Continue the phase, fix the blockers, run the phase-gate skill again, and end with the final status line." "$sid"
    status=$(phase_status "$out")
    sid=$(json_field "$out" session_id)
  done
  [[ "$status" == DONE* ]] || die "Phase $n is blocked: ${status:-unknown}. Logs: $LOG_DIR/phase-$n-*.json"

  if [[ -n "$(git status --porcelain)" ]]; then
    git add -A
    git commit -qm "chore(phase-$n): commit remaining autopilot changes"
  fi
  if (( num == 6 )); then lock_baseline; fi

  ship_phase "$branch" "$num"
  touch "$STATE_DIR/$id.done"
}

main() {
  preflight
  local id
  for id in "${PHASES[@]}"; do
    if [[ -n "$ONLY_PHASE" && $((10#${id%%-*})) -ne $((10#$ONLY_PHASE)) ]]; then continue; fi
    run_phase "$id"
  done
  log "Autopilot finished. Next: read _private/OWNER_REVIEW.md, relabel evals/reference_labels as your own, write the README problem paragraph, deploy the demo (docs/DEPLOY.md), record the GIF."
}

main "$@"
