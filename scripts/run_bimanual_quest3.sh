#!/usr/bin/env bash
# Launch real dual-Piper Quest3 teleoperation over HTTPS/WSS.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate lerobot

export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"
unset VUER_HTTP

LEFT_CAN="${LEFT_CAN:-can_left}"
RIGHT_CAN="${RIGHT_CAN:-can_right}"
VUER_PORT="${VUER_PORT:-8012}"

for can_name in "${LEFT_CAN}" "${RIGHT_CAN}"; do
  if ! ip -br link show "${can_name}" 2>/dev/null | grep -q "UP"; then
    echo "[can] Interface '${can_name}' is not UP." >&2
    exit 1
  fi
done

for tls_file in teleop/cert.pem teleop/key.pem; do
  if [[ ! -f "${tls_file}" ]]; then
    echo "[tls] Missing ${REPO_ROOT}/${tls_file}" >&2
    exit 1
  fi
done

mkdir -p Log
LOG_FILE="Log/bimanual_quest3_real_$(date +%Y%m%d_%H%M%S).log"

echo "Dual-arm REAL teleoperation"
echo "  left controller  -> ${LEFT_CAN}"
echo "  right controller -> ${RIGHT_CAN}"
echo "  Quest URL: https://localhost:${VUER_PORT}?ws=wss://localhost:${VUER_PORT}"
echo "  log: ${REPO_ROOT}/${LOG_FILE}"

# Keep Python as the foreground process so SIGINT/SIGTERM reaches its cleanup
# handler. Process substitution preserves a complete console log without making
# tee the pipeline's foreground command.
exec > >(tee -a "${LOG_FILE}") 2>&1
exec python scripts/teleop_bimanual_quest3.py \
  --left-can "${LEFT_CAN}" \
  --right-can "${RIGHT_CAN}"
