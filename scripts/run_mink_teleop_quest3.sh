#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# MINK IK + Quest3 VR teleoperation launcher (right arm).
#
# Pipeline:
#   Quest3 controller pose  ->  Vuer HTTPS/WSS server (this host)
#     ->  VRToRobotMapper (EE target)  ->  MINK QP IK (MuJoCo)
#     ->  6 joint angles  ->  piper_sender process  ->  Piper CAN driver
#
# Right VR controller -> right Piper arm (can_right).
#
# Safety: runs in --dry-run by default (no hardware commands; verifies Quest3
# connection + MINK IK + MuJoCo viewer only). Pass --real to drive the arm.
#
# Usage:
#   scripts/run_mink_teleop_quest3.sh            # dry-run (safe, default)
#   scripts/run_mink_teleop_quest3.sh --real     # drive the real right arm
#   CAN=can_left scripts/run_mink_teleop_quest3.sh --real   # left arm instead
#
# Prereqs:
#   - conda env 'lerobot'
#   - CAN up:  can_right   (see can_activate.sh)
#   - Quest3 on the SAME network as this host
#   - HTTPS cert/key under teleop/ (auto-restored from *.pre-usb if missing)
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# ---- args --------------------------------------------------------------
DRY_RUN=1
for arg in "$@"; do
  case "${arg}" in
    --real)     DRY_RUN=0 ;;
    --dry-run)  DRY_RUN=1 ;;
    *) echo "Unknown argument: ${arg}" >&2; exit 2 ;;
  esac
done

CAN="${CAN:-can_right}"
VUER_PORT="${VUER_PORT:-8012}"
CAMERA="${CAMERA:-6}"
# USB=1 -> use HTTPS/WSS over adb reverse. The Vuer frontend upgrades its
# websocket URL to wss://, so a plain HTTP/WS backend leaves VR disconnected.
USB="${USB:-0}"

# ---- conda -------------------------------------------------------------
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate lerobot

export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

# ---- HTTPS cert for Quest3 (Vuer needs cert.pem + key.pem) --------------
unset VUER_HTTP
CERT="${REPO_ROOT}/teleop/cert.pem"
KEY="${REPO_ROOT}/teleop/key.pem"
if [[ ! -f "${CERT}" || ! -f "${KEY}" ]]; then
  if [[ -f "${CERT}.pre-usb" && -f "${KEY}.pre-usb" ]]; then
    echo "[cert] cert.pem/key.pem missing -> restoring from *.pre-usb backup"
    [[ -f "${CERT}" ]] || cp "${CERT}.pre-usb" "${CERT}"
    [[ -f "${KEY}"  ]] || cp "${KEY}.pre-usb"  "${KEY}"
  else
    echo "[cert] Generating a self-signed cert for Vuer HTTPS ..."
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
      -keyout "${KEY}" -out "${CERT}" -subj "/CN=localhost" \
      -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" >/dev/null 2>&1
  fi
fi

# ---- CAN sanity (real mode only) ---------------------------------------
if [[ "${DRY_RUN}" -eq 0 ]]; then
  if ! ip -br link show "${CAN}" 2>/dev/null | grep -q "UP"; then
    echo "[can] Interface '${CAN}' is not UP." >&2
    echo "      Bring it up first, e.g.:  bash can_activate.sh ${CAN}" >&2
    exit 1
  fi
fi

# ---- Quest3 connection hint --------------------------------------------
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "======================================================================"
if [[ "${DRY_RUN}" -eq 1 ]]; then
  echo " MODE : DRY-RUN  (no hardware — verifying VR + MINK IK only)"
else
  echo " MODE : REAL     (driving Piper right arm on ${CAN})"
fi
echo " ARM  : right VR controller -> ${CAN}"
echo " IK   : MINK (MuJoCo QP differential IK)"
echo " CAM  : /dev/video${CAMERA}"
echo ""
if [[ "${USB}" == "1" ]]; then
  echo " LINK : USB cable (adb reverse). On the Quest3 browser open:"
  echo "     https://localhost:${VUER_PORT}?ws=wss://localhost:${VUER_PORT}"
  echo " (accept the certificate warning once before entering VR)"
  echo " (run scripts/quest3_usb_link.sh first if not done)"
else
  echo " LINK : WiFi. On the Quest3 browser open:"
  echo "     https://${HOST_IP:-<this-host-ip>}:${VUER_PORT}"
  echo " (accept the self-signed certificate warning, then Enter VR)"
fi
echo "======================================================================"

# ---- logging -----------------------------------------------------------
LOG_DIR="${REPO_ROOT}/Log"
mkdir -p "${LOG_DIR}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SUFFIX=$([[ "${DRY_RUN}" -eq 1 ]] && echo "dryrun" || echo "real")
LOG_FILE="${LOG_DIR}/mink_teleop_${SUFFIX}_${TIMESTAMP}.log"
echo "Logging to: ${LOG_FILE}"
echo "Start time: $(date)" | tee -a "${LOG_FILE}"

# ---- run ---------------------------------------------------------------
CMD=(python -m teleop.teleop_real_arm --can "${CAN}" --camera "${CAMERA}")
[[ "${DRY_RUN}" -eq 1 ]] && CMD+=(--dry-run)

echo "Command: ${CMD[*]}" | tee -a "${LOG_FILE}"
exec "${CMD[@]}" 2>&1 | tee -a "${LOG_FILE}"
