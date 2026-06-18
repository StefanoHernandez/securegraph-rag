#!/usr/bin/env bash
# Live snapshot of the running baseline.
# Usage: bash eval/baseline_progress.sh [task_output_file]
# Default: most recent baseline task output under /tmp/claude-1000/.../tasks/

set -u

LOG="${1:-}"
if [ -z "$LOG" ]; then
  # pick the most recent task output that contains the baseline signature
  for f in $(ls -t /tmp/claude-1000/-home-stefano-Documenti-ProgettiPersonali-securegraph-rag/*/tasks/*.output 2>/dev/null); do
    if grep -q 'Loaded 30 QA pairs' "$f" 2>/dev/null; then
      LOG="$f"
      break
    fi
  done
fi
if [ -z "$LOG" ] || [ ! -f "$LOG" ]; then
  echo "no baseline task output file found"
  exit 1
fi
echo "log: $LOG"

PID=$(pgrep -f 'baseline.py' | head -1)
if [ -n "$PID" ]; then
  ETIME=$(ps -o etime= -p "$PID" 2>/dev/null | tr -d ' ')
  echo "baseline PID=$PID  elapsed=$ETIME"
else
  echo "baseline process NOT running"
fi

INGESTED=$(grep -c 'ingest_completed' "$LOG" 2>/dev/null || echo 0)
echo "ingest:   $INGESTED / 5 docs"

# detect current phase by the last "--- ... ---" banner line
LAST_BANNER=$(grep -E '^--- ' "$LOG" 2>/dev/null | tail -1)
echo "phase:    ${LAST_BANNER:-?}"

# count retries triggered
RETRIES=$(grep -c 'extract_zero_relations_retrying' "$LOG" 2>/dev/null || echo 0)
echo "retries:  $RETRIES  (gemma4 think=True empty-relations safety-net activations)"

# count extract failures (claim decomposition JSON errors — non-blocking)
EXTRACT_FAIL=$(grep -c 'extract_claims_failed' "$LOG" 2>/dev/null || echo 0)
echo "extract_claims_failed warnings: $EXTRACT_FAIL  (non-blocking; fallback to empty claim list)"

# rough RAGAS progress: count of "Evaluating:" tqdm-like lines if present
if grep -q 'Computing RAGAS' "$LOG" 2>/dev/null; then
  echo "RAGAS:   started"
  # ragas prints "Evaluating: X% [...]" — grab the last percent
  LAST_PCT=$(grep -oE 'Evaluating: *[0-9]+%' "$LOG" 2>/dev/null | tail -1)
  if [ -n "$LAST_PCT" ]; then
    echo "  $LAST_PCT (most-recent metric stage)"
  fi
fi

# tail last 5 non-INFO log lines
echo ""
echo "--- last 5 lines ---"
tail -5 "$LOG"
