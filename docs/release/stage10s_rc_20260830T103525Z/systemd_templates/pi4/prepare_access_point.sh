#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--apply" ]]; then
  echo "DRY RUN ONLY"
  echo "This Pi-only script will create a NetworkManager Wi-Fi access point when called with --apply."
  echo "Required environment: RESCUESWARM_AP_PASSWORD (8+ characters)."
  exit 0
fi

if [[ ! -r /proc/device-tree/model ]] || ! tr -d '\0' </proc/device-tree/model | grep -q "Raspberry Pi"; then
  echo "Refusing: this script is intended only for a Raspberry Pi." >&2
  exit 1
fi
if [[ $EUID -ne 0 ]]; then
  echo "Refusing: rerun with approved root privileges on the Pi." >&2
  exit 1
fi
: "${RESCUESWARM_AP_PASSWORD:?Set RESCUESWARM_AP_PASSWORD in the local shell; do not commit it}"
if [[ ${#RESCUESWARM_AP_PASSWORD} -lt 8 ]]; then
  echo "Refusing: access-point password must contain at least 8 characters." >&2
  exit 1
fi

SSID="${RESCUESWARM_AP_SSID:-RescueSwarm-Local}"
GROUND_IP="${RESCUESWARM_GROUND_IP:-10.42.0.1/24}"
INTERFACE="${RESCUESWARM_WIFI_INTERFACE:-wlan0}"
CONNECTION_NAME="rescueswarm-ap"

nmcli connection show "$CONNECTION_NAME" >/dev/null 2>&1 && {
  echo "Refusing to overwrite existing NetworkManager connection '$CONNECTION_NAME'." >&2
  exit 1
}
nmcli connection add type wifi ifname "$INTERFACE" con-name "$CONNECTION_NAME" autoconnect yes ssid "$SSID"
nmcli connection modify "$CONNECTION_NAME" 802-11-wireless.mode ap 802-11-wireless.band bg \
  ipv4.method shared ipv4.addresses "$GROUND_IP" ipv6.method disabled
nmcli connection modify "$CONNECTION_NAME" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$RESCUESWARM_AP_PASSWORD"
nmcli connection up "$CONNECTION_NAME"
echo "Access point enabled. Verify local client connectivity before competition use."

