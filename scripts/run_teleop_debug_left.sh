#!/usr/bin/env bash
# Run original teleop with logging.
# Usage: bash scripts/run_teleop_debug.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${REPO_ROOT}/Log"
mkdir -p "${LOG_DIR}"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="${LOG_DIR}/teleop_debug_${TIMESTAMP}.log"

echo "Logging to: ${LOG_FILE}"
echo "Start time: $(date)" | tee -a "${LOG_FILE}"

source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate lerobot

cd "${REPO_ROOT}"
export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

python -m teleop.teleop_real_arm --can can_left 2>&1 | tee -a "${LOG_FILE}"
