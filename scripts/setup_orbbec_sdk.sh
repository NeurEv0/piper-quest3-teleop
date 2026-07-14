#!/usr/bin/env bash
# Vendor the Orbbec SDK shared libraries into this workspace so recording does
# not depend on the piper_lerobot-main fork tree for a runtime binary.
#
# The SDK is built with RPATH=$ORIGIN, so copying the whole lib directory keeps
# its internal inter-library references (liblive555.so, libob_usb.so, ...) valid
# at the new location.
#
# Usage:
#   scripts/setup_orbbec_sdk.sh [SOURCE_LIB_DIR]
#
# If SOURCE_LIB_DIR is omitted, the known fork install path is used.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DST="${REPO_ROOT}/third_party/orbbec_sdk/lib"

DEFAULT_SRC="/home/ylhp-e-ai/piper_lerobot-main/src/OrbbecSDK_ROS2-main/install/orbbec_camera/lib"
SRC="${1:-$DEFAULT_SRC}"

if [[ ! -e "${SRC}/libOrbbecSDK.so" ]]; then
  echo "ERROR: libOrbbecSDK.so not found under '${SRC}'." >&2
  echo "Pass the correct source lib dir as the first argument." >&2
  exit 1
fi

echo "Vendoring Orbbec SDK:"
echo "  from: ${SRC}"
echo "  to:   ${DST}"
mkdir -p "${DST}"
# -a preserves symlinks (libOrbbecSDK.so -> .so.1.10 -> .so.1.10.35)
cp -a "${SRC}"/. "${DST}"/
# Drop ROS2 node executables — the ctypes wrapper only needs the .so files.
rm -rf "${DST}/orbbec_camera"

echo "Done. Vendored libraries:"
ls -1 "${DST}"

# Quick load check (independence verification).
python - "$DST/libOrbbecSDK.so" <<'PY' || echo "WARNING: vendored SDK failed to load via ctypes."
import ctypes, sys
from pathlib import Path
p = Path(sys.argv[1]).resolve()
ctypes.CDLL(str(p), mode=ctypes.RTLD_GLOBAL)
print(f"OK: {p} loads standalone.")
PY
