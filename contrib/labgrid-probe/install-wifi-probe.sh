#!/bin/sh
# Install the Wi-Fi probe (labgrid-wifi-probe) on an OpenWrt device over SSH,
# from the operator's machine. Not Ansible: Ansible needs python3 on the
# device, which doesn't fit in the TL-WDR4300's 8 MB of flash.
#
#   sh contrib/labgrid-probe/install-wifi-probe.sh root@192.168.128.4 -J labgrid-aparcar
#
# Copies the Wi-Fi probe's files, installs missing packages (ip-full; the
# rest comes with release images), disables the radios for netifd (the probe
# creates its own interfaces on them), compile-checks the code, restarts rpcd
# and checks the ubus object, its ACL and uhttpd. Run it again after changing
# the files. Arguments after the destination go to ssh.
set -eu

[ $# -ge 1 ] || {
	echo "usage: $0 <user@host> [ssh options]" >&2
	exit 2
}
dest=$1
shift
files=$(dirname "$0")/files

FILES="usr/share/ucode/labgrid_probe_wifi.uc
usr/share/rpcd/ucode/labgrid-wifi-probe.uc
usr/share/rpcd/acl.d/labgrid-wifi-probe.json
usr/libexec/labgrid-probe/wifi-sta
usr/share/udhcpc/labgrid-wifi-probe.script"

# COPYFILE_DISABLE: no macOS ._ metadata files in the archive
# shellcheck disable=SC2086 # FILES is a list
COPYFILE_DISABLE=1 tar -C "$files" -cf - $FILES | ssh "$@" "$dest" 'tar -C / -xf -'

ssh "$@" "$dest" sh -s <<'EOF'
set -e

missing=""
for p in ip-full ucode ucode-mod-fs rpcd rpcd-mod-ucode uhttpd uhttpd-mod-ubus; do
	apk info -e "$p" >/dev/null 2>&1 || missing="$missing $p"
done
if [ -n "$missing" ]; then
	apk update
	apk add $missing
fi

chown root:root /usr/share/ucode/labgrid_probe_wifi.uc \
	/usr/share/rpcd/ucode/labgrid-wifi-probe.uc \
	/usr/share/rpcd/acl.d/labgrid-wifi-probe.json \
	/usr/libexec/labgrid-probe/wifi-sta \
	/usr/share/udhcpc/labgrid-wifi-probe.script

changed=""
for radio in $(uci -q show wireless | sed -n 's/^wireless\.\([^.=]*\)=wifi-device$/\1/p'); do
	[ "$(uci -q get "wireless.$radio.disabled")" = 1 ] && continue
	uci set "wireless.$radio.disabled=1"
	changed=1
done
if [ -n "$changed" ]; then
	uci commit wireless
	wifi
	echo "radios disabled for netifd"
fi

# A firewall zone for the stations (ws<n>s<i>): without one, fw4's default
# input policy rejects new traffic to them (a station pinging another one
# gets ICMP unreachable); replies to their own traffic pass either way
if [ "$(uci -q get firewall.wifi_probe)" != zone ]; then
	uci set firewall.wifi_probe=zone
	uci set firewall.wifi_probe.name=wprobe
	uci add_list firewall.wifi_probe.device='ws+'
	uci set firewall.wifi_probe.input=ACCEPT
	uci set firewall.wifi_probe.output=ACCEPT
	uci set firewall.wifi_probe.forward=REJECT
	uci commit firewall
	/etc/init.d/firewall reload
	echo "firewall zone wprobe (ws+) added"
fi

# Software crypto: all stations of a set talk to the same AP, and ath9k's
# hardware key cache finds a pairwise key by the peer's address, so with
# hardware crypto only one station's unicast frames decrypt (the others
# associate but never get a DHCP offer). Takes effect at the next boot.
reboot_needed=""
if [ -f /etc/modules.d/ath9k ]; then
	grep -q 'nohwcrypt=1' /etc/modules.d/ath9k || echo 'ath9k nohwcrypt=1' >/etc/modules.d/ath9k
	[ "$(cat /sys/module/ath9k/parameters/nohwcrypt 2>/dev/null)" = 1 ] || reboot_needed=1
fi

# rpcd silently skips plugins that don't compile
for f in /usr/share/rpcd/ucode/labgrid-wifi-probe.uc /usr/libexec/labgrid-probe/wifi-sta \
	/usr/share/udhcpc/labgrid-wifi-probe.script; do
	if ! out=$(ucode -c -o /dev/null "$f" 2>&1) || [ -n "$out" ]; then
		echo "ucode compile failed: $f"
		echo "$out"
		exit 1
	fi
done

/etc/init.d/rpcd restart
sleep 2
ubus -S list labgrid-wifi-probe >/dev/null 2>&1 || {
	echo "ubus object labgrid-wifi-probe missing; run rpcd in the foreground to see why:"
	echo "  /etc/init.d/rpcd stop; rpcd; /etc/init.d/rpcd start"
	exit 1
}
ubus call session access '{"ubus_rpc_session":"00000000000000000000000000000000","scope":"ubus","object":"labgrid-wifi-probe","function":"radios"}' \
	| grep -q '"access": true' || {
	echo "the unauthenticated session may not call labgrid-wifi-probe: check /usr/share/rpcd/acl.d/labgrid-wifi-probe.json"
	exit 1
}
pidof uhttpd >/dev/null || {
	echo "uhttpd is not running"
	exit 1
}
uci -q get uhttpd.main.ubus_prefix >/dev/null || echo "warning: uhttpd has no ubus_prefix (/ubus): tests can't reach the probe"

ubus call labgrid-wifi-probe radios

if [ -n "$reboot_needed" ]; then
	echo "rebooting for ath9k nohwcrypt=1 (software crypto)"
	reboot
fi
EOF
