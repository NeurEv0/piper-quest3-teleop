#!/usr/bin/env bash
# Internal wrapper used by scripts/start_vla_capture.sh to enable the raw MCAP sidecar.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ENABLE_MCAP=1
exec "${SCRIPT_DIR}/run_bimanual_canonical.sh" "$@"
