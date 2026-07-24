#!/usr/bin/env bash
# User-facing launcher for the three initial cube-stacking VLA tasks.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

TASK_CHOICE=""
OPERATOR_ID="${OPERATOR_ID:-operator}"
SCENE_ID="${SCENE_ID:-cube_stack_table}"
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: bash scripts/start_vla_capture.sh [options]

Options:
  --task 1|2|3       Select a task without the interactive menu
  --operator ID      Operator ID (default: operator)
  --scene ID         Scene ID (default: cube_stack_table)
  --dry-run          Print the resolved configuration without starting hardware
  -h, --help         Show this help

Canonical Raw and MCAP are both enabled by default.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --task) TASK_CHOICE="${2:-}"; shift 2 ;;
    --operator) OPERATOR_ID="${2:-}"; shift 2 ;;
    --scene) SCENE_ID="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "${TASK_CHOICE}" ]]; then
  echo "Select a VLA task:"
  echo "  1) Stack the white cube on the mint green cube."
  echo "  2) Stack the mint green cube on the white cube."
  echo "  3) Stack the small cube on the big cube."
  read -r -p "Task [1]: " TASK_CHOICE
  TASK_CHOICE="${TASK_CHOICE:-1}"
fi

case "${TASK_CHOICE}" in
  1)
    TASK_ID="stack_white_on_mint_green"
    INSTRUCTION="Stack the white cube on the mint green cube."
    ;;
  2)
    TASK_ID="stack_mint_green_on_white"
    INSTRUCTION="Stack the mint green cube on the white cube."
    ;;
  3)
    TASK_ID="stack_small_on_big"
    INSTRUCTION="Stack the small cube on the big cube."
    ;;
  *)
    echo "Task must be 1, 2, or 3." >&2
    exit 2
    ;;
esac

echo
echo "Piper VLA capture"
echo "  Task        : ${TASK_CHOICE} (${TASK_ID})"
echo "  Instruction : ${INSTRUCTION}"
echo "  Operator    : ${OPERATOR_ID}"
echo "  Scene       : ${SCENE_ID}"
echo "  Canonical   : enabled"
echo "  MCAP        : enabled"
echo "  Dashboard   : http://localhost:8020"
echo "  Quest3      : https://localhost:8012?ws=wss://localhost:8012"
echo

COMMAND=(
  bash "${SCRIPT_DIR}/run_bimanual_mcap_shadow.sh"
  --operator-id "${OPERATOR_ID}"
  --task-id "${TASK_ID}"
  --scene-id "${SCENE_ID}"
  --language-instruction "${INSTRUCTION}"
)

if [[ "${DRY_RUN}" == "1" ]]; then
  printf 'Command      :'
  printf ' %q' "${COMMAND[@]}"
  printf '\n'
  exit 0
fi

if pgrep -f 'python .*scripts/record_bimanual_canonical.py' >/dev/null 2>&1; then
  echo "A Canonical capture process is already running." >&2
  echo "Stop it cleanly before starting another hardware process." >&2
  exit 1
fi

cd "${REPO_ROOT}"
exec "${COMMAND[@]}"
