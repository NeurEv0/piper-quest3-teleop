#!/usr/bin/env bash
# Prepare both Piper USB-CAN adapters with stable names before hardware startup.
set -euo pipefail

LEFT_SERIAL="${PIPER_LEFT_CAN_SERIAL:-001A002A5547570420303135}"
RIGHT_SERIAL="${PIPER_RIGHT_CAN_SERIAL:-003000495547571120343930}"
BITRATE="${PIPER_CAN_BITRATE:-1000000}"
ESTOP_SERVICE="${PIPER_ESTOP_SERVICE:-piper-emergency-stop.service}"

if [[ "${EUID}" -ne 0 ]]; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "[can] sudo is required to configure CAN interfaces." >&2
    exit 1
  fi
  exec sudo --preserve-env=PIPER_LEFT_CAN_SERIAL,PIPER_RIGHT_CAN_SERIAL,PIPER_CAN_BITRATE,PIPER_ESTOP_SERVICE \
    bash "$0" "$@"
fi

find_interface_by_serial() {
  local wanted="$1"
  local path iface serial
  for path in /sys/class/net/*; do
    [[ -e "${path}" ]] || continue
    iface="${path##*/}"
    serial="$(udevadm info -q property -p "${path}" 2>/dev/null \
      | sed -n 's/^ID_SERIAL_SHORT=//p' | head -n 1)"
    if [[ "${serial}" == "${wanted}" ]]; then
      printf '%s\n' "${iface}"
      return 0
    fi
  done
  return 1
}

LEFT_IFACE="$(find_interface_by_serial "${LEFT_SERIAL}" || true)"
RIGHT_IFACE="$(find_interface_by_serial "${RIGHT_SERIAL}" || true)"

if [[ -z "${LEFT_IFACE}" ]]; then
  echo "[can] Left USB-CAN adapter not found (serial ${LEFT_SERIAL})." >&2
  exit 1
fi
if [[ -z "${RIGHT_IFACE}" ]]; then
  echo "[can] Right USB-CAN adapter not found (serial ${RIGHT_SERIAL})." >&2
  exit 1
fi
if [[ "${LEFT_IFACE}" == "${RIGHT_IFACE}" ]]; then
  echo "[can] Left and right serials resolved to the same interface." >&2
  exit 1
fi

echo "[can] left  serial ${LEFT_SERIAL}: ${LEFT_IFACE} -> can_left"
echo "[can] right serial ${RIGHT_SERIAL}: ${RIGHT_IFACE} -> can_right"

# Temporary names make the operation safe even if Linux enumerated adapters in
# the opposite order or retained the stable names from an earlier run.
ip link set "${LEFT_IFACE}" down
ip link set "${RIGHT_IFACE}" down
ip link set "${LEFT_IFACE}" name pcan_l_tmp
ip link set "${RIGHT_IFACE}" name pcan_r_tmp
ip link set pcan_l_tmp name can_left
ip link set pcan_r_tmp name can_right

for iface in can_left can_right; do
  ip link set "${iface}" down
  ip link set "${iface}" type can bitrate "${BITRATE}"
  ip link set "${iface}" txqueuelen 1000
  ip link set "${iface}" up
done

for iface in can_left can_right; do
  if ! ip -details link show "${iface}" | grep -q 'can state ERROR-ACTIVE'; then
    echo "[can] ${iface} is not ERROR-ACTIVE after configuration." >&2
    ip -details link show "${iface}" >&2 || true
    exit 1
  fi
done

if systemctl list-unit-files "${ESTOP_SERVICE}" --no-legend 2>/dev/null | grep -q "${ESTOP_SERVICE}"; then
  systemctl restart "${ESTOP_SERVICE}"
  for _ in {1..30}; do
    if systemctl is-active --quiet "${ESTOP_SERVICE}"; then
      echo "[can] Emergency-stop service is active."
      exit 0
    fi
    sleep 0.1
  done
  echo "[can] Emergency-stop service failed to become active." >&2
  systemctl status "${ESTOP_SERVICE}" --no-pager -l >&2 || true
  exit 1
fi

echo "[can] Emergency-stop service unit ${ESTOP_SERVICE} was not found." >&2
exit 1
