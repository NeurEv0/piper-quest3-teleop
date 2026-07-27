#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

CONDA_BASE="${PIPER_CONDA_BASE:-/home/ylhp-e-ai/miniconda3}"
CONDA_INIT="${CONDA_BASE}/etc/profile.d/conda.sh"
if [[ ! -f "${CONDA_INIT}" ]]; then
  echo "[env] Missing Conda initialization script: ${CONDA_INIT}" >&2
  exit 1
fi
source "${CONDA_INIT}"
conda activate lerobot

export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"
if [[ "${AUTO_PREPARE_CAN:-1}" == "1" ]]; then
  bash scripts/prepare_piper_can.sh
fi

exec python scripts/replay_bimanual_processed.py "$@"
