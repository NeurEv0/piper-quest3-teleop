#!/usr/bin/env bash
# Stop the Canonical Raw / MCAP capture service without leaving stale ports.
set -euo pipefail

FORCE=0
DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      cat <<'EOF'
Usage: bash scripts/stop_vla_capture.sh [--force] [--dry-run]

Normal stop refuses while an episode is recording, preserving the data.
Use --force only after accepting that an active episode will be aborted.
EOF
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

PROCESS_PATTERN='^python scripts/record_bimanual_canonical.py( |$)'
PIDS="$(pgrep -f "${PROCESS_PATTERN}" || true)"
if [[ -z "${PIDS}" ]]; then
  echo "No Canonical capture process is running."
  exit 0
fi

STATE="$(curl -fsS --max-time 3 http://127.0.0.1:8020/api/status 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["recorder"]["state"])' \
  2>/dev/null || true)"
if [[ "${STATE}" == "RECORDING" && "${FORCE}" != "1" ]]; then
  echo "An episode is currently RECORDING; finish or abort it from the dashboard first." >&2
  echo "Use --force only if you intentionally want to discard the active episode." >&2
  exit 1
fi

echo "Capture process group(s): ${PIDS//$'\n'/ }"
if [[ "${DRY_RUN}" == "1" ]]; then
  echo "Would request a graceful shutdown, then verify ports 8012 and 8020."
  exit 0
fi

for pid in ${PIDS}; do
  kill -TERM "${pid}" 2>/dev/null || true
done

deadline=$((SECONDS + 15))
while (( SECONDS < deadline )); do
  if ! pgrep -f "${PROCESS_PATTERN}" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

REMAINING="$(pgrep -f "${PROCESS_PATTERN}" || true)"
if [[ -n "${REMAINING}" ]]; then
  echo "Graceful shutdown timed out; terminating stale capture processes." >&2
  for pid in ${REMAINING}; do kill -KILL "${pid}" 2>/dev/null || true; done
  sleep 1
fi

if ss -ltn 2>/dev/null | grep -Eq ':(8012|8020)\b'; then
  echo "Ports 8012/8020 are still occupied; inspect with: ss -ltnp | grep -E ':(8012|8020)'" >&2
  exit 1
fi
echo "Canonical capture stopped; ports 8012 and 8020 are free."
