#!/usr/bin/env bash
# Opt-in launcher: preserve Canonical Raw and add a raw MCAP sidecar.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ENABLE_MCAP=1
exec "${SCRIPT_DIR}/run_bimanual_canonical.sh" "$@"
