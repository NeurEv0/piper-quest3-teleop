#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# USB link for Quest3 <-> this PC (adb reverse), so the headset browser can
# reach the Vuer teleop server at https://localhost:8012 over the USB cable
# instead of WiFi.
#
# The current Vuer server serves HTTPS and WSS on 8012. The operator
# dashboard is also forwarded on 8020 for USB-only operation.
#
# Run this ONCE after plugging in the Quest3 (and re-run if you replug the
# cable or reboot the headset). Then start teleop as usual.
#
# Usage:
#   scripts/quest3_usb_link.sh          # set up forwarding
#   scripts/quest3_usb_link.sh --status # show current forwards
#   scripts/quest3_usb_link.sh --clear  # remove forwarding
# ---------------------------------------------------------------------------
set -euo pipefail

read -r -a PORTS <<< "${QUEST_USB_PORTS:-8012 8020}"

# Pick the real Quest3 and ignore offline emulators or unrelated Android devices.
QUEST="$(adb devices -l | awk '$2=="device" && ($0 ~ /model:Quest_3/ || $0 ~ /product:eureka/) {print $1; exit}')"
if [[ -z "${QUEST:-}" ]]; then
  echo "[usb] No authorized adb device found." >&2
  echo "      Plug in the Quest3, put it on, and accept the USB debugging prompt." >&2
  adb devices
  exit 1
fi
echo "[usb] Quest3 device: ${QUEST}"

case "${1:-}" in
  --status)
    adb -s "${QUEST}" reverse --list
    exit 0
    ;;
  --clear)
    for p in "${PORTS[@]}"; do adb -s "${QUEST}" reverse --remove "tcp:${p}" 2>/dev/null || true; done
    echo "[usb] Cleared. Current forwards:"
    adb -s "${QUEST}" reverse --list
    exit 0
    ;;
esac

for p in "${PORTS[@]}"; do
  adb -s "${QUEST}" reverse "tcp:${p}" "tcp:${p}"
  echo "[usb]  tcp:${p} -> localhost:${p}  OK"
done

echo ""
echo "======================================================================"
echo " USB link ready. In the Quest3 browser open:"
echo "     https://localhost:8012?ws=wss://localhost:8012"
echo "     Accept the certificate warning once, then enter VR."
echo "     Operator dashboard: http://localhost:8020"
echo ""
echo " Start teleop with USB=1 so it serves HTTPS/WSS over adb reverse:"
echo "     USB=1 bash scripts/run_mink_teleop_quest3.sh"
echo "======================================================================"
adb -s "${QUEST}" reverse --list
