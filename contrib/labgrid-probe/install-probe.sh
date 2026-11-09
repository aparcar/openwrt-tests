#!/bin/sh
# Install or update a probe on an OpenWrt device over SSH, from the
# operator's machine. Not Ansible: probes are plain OpenWrt devices without
# python3 (the Wi-Fi probe's 8 MB of flash has no room for it).
#
#   sh contrib/labgrid-probe/install-probe.sh lan  root@192.168.128.3 -J labgrid-aparcar
#   sh contrib/labgrid-probe/install-probe.sh wifi root@192.168.128.4 -J labgrid-aparcar
#   sh contrib/labgrid-probe/install-probe.sh local --parent eth1 --parent eth0 root@<lab host>
#
#   lan    the wired probe of its own (ubus object labgrid-probe, trunk mode:
#          slots are VLANs on its trunk port), e.g. a GL-MT2500.
#          --config (first install only) also installs its network,
#          firewall and system config (files/etc), replacing the device's;
#          the network config applies at the next reboot.
#   local  the wired probe on an OpenWrt lab host (local mode: slots are
#          macvlans on the host's DUT interfaces). --parent for each DUT
#          interface slots may use: the %<dev> of the DUTs' NetworkService
#          and the ifname of their NetworkInterfaces in the lab's
#          exporter.yaml. Adds a uhttpd instance on 127.0.0.1:8080 (the
#          probe place's NetworkService) and forwards the DUTs' internet
#          uplinks to the firewall zone `wan`; the host's network stays the
#          lab admin's.
#   wifi   the Wi-Fi client probe (labgrid-wifi-probe), a TL-WDR4300: also
#          disables the radios for netifd (the probe creates its own
#          interfaces on them), adds the firewall zone for the stations and
#          sets ath9k's software crypto (one reboot).
#
# Copies the probe's files, installs missing packages, compile-checks the
# code, restarts rpcd and checks the ubus object, its ACL and uhttpd. Run it
# again after changing the files. Arguments after the destination go to ssh.
set -eu

usage() {
	echo "usage: $0 lan [--config] | local --parent <dev>... | wifi  <user@host> [ssh options]" >&2
	exit 2
}

[ $# -ge 2 ] || usage
kind=$1
shift
config=""
parents=""
while [ $# -gt 0 ]; do
	case "$1" in
	--config)
		[ "$kind" = lan ] || usage
		config=1
		shift
		;;
	--parent)
		[ "$kind" = local ] && [ $# -ge 2 ] || usage
		parents="$parents $2"
		shift 2
		;;
	*)
		break
		;;
	esac
done
[ $# -ge 1 ] || usage
[ "$kind" != local ] || [ -n "$parents" ] || usage
dest=$1
shift
files=$(dirname "$0")/files

case "$kind" in
lan | local)
	FILES="usr/share/ucode/labgrid_probe.uc
usr/share/ucode/labgrid_probe_ra.uc
usr/share/rpcd/ucode/labgrid-probe.uc
usr/share/rpcd/acl.d/labgrid-probe.json
usr/share/udhcpc/labgrid-probe.script
usr/libexec/labgrid-probe/ra-capture
usr/libexec/labgrid-probe/mcast"
	if [ -n "$config" ]; then
		FILES="$FILES
etc/config/network
etc/config/firewall
etc/config/system
etc/uci-defaults/90-labgrid-probe"
	fi
	;;
wifi)
	FILES="usr/share/ucode/labgrid_probe_wifi.uc
usr/share/rpcd/ucode/labgrid-wifi-probe.uc
usr/share/rpcd/acl.d/labgrid-wifi-probe.json
usr/libexec/labgrid-probe/wifi-sta
usr/share/udhcpc/labgrid-wifi-probe.script"
	;;
*)
	usage
	;;
esac

# COPYFILE_DISABLE: no macOS ._ metadata files in the archive. -o (busybox
# tar): the files belong to root, not to the operator's uid
# shellcheck disable=SC2086 # FILES is a list
COPYFILE_DISABLE=1 tar -C "$files" -cf - $FILES | ssh "$@" "$dest" 'tar -C / -oxf -'

# shellcheck disable=SC2086 # parents is a list
ssh "$@" "$dest" sh -s -- "$kind" $parents <<'EOF'
set -e
kind=$1
shift

case "$kind" in
lan | local)
	object=labgrid-probe
	check=info
	pkgs="ip-full iperf3 kmod-veth ucode ucode-mod-fs ucode-mod-uci ucode-mod-socket
		ucode-mod-struct rpcd rpcd-mod-ucode uhttpd uhttpd-mod-ubus"
	[ "$kind" = local ] && pkgs="$pkgs kmod-macvlan"
	compile="/usr/share/rpcd/ucode/labgrid-probe.uc /usr/share/udhcpc/labgrid-probe.script
		/usr/libexec/labgrid-probe/ra-capture /usr/libexec/labgrid-probe/mcast"
	;;
wifi)
	object=labgrid-wifi-probe
	check=radios
	# The rest comes with release images
	pkgs="ip-full ucode ucode-mod-fs rpcd rpcd-mod-ucode uhttpd uhttpd-mod-ubus"
	compile="/usr/share/rpcd/ucode/labgrid-wifi-probe.uc /usr/libexec/labgrid-probe/wifi-sta
		/usr/share/udhcpc/labgrid-wifi-probe.script"
	;;
esac

missing=""
if command -v apk >/dev/null; then
	for p in $pkgs; do
		apk info -e "$p" >/dev/null 2>&1 || missing="$missing $p"
	done
	if [ -n "$missing" ]; then
		apk update
		apk add $missing
	fi
else
	for p in $pkgs; do
		opkg list-installed "$p" | grep -q . || missing="$missing $p"
	done
	if [ -n "$missing" ]; then
		opkg update
		opkg install $missing
	fi
fi

restart_uhttpd=""
reboot_needed=""
case "$kind" in
lan | local)
	# Earlier name of the probe
	rm -f /usr/share/ucode/lanclient.uc /usr/share/rpcd/ucode/lanclient.uc \
		/usr/share/rpcd/acl.d/lanclient.json /usr/bin/lanclient \
		/usr/share/udhcpc/lanclient.script

	# Read by labgrid_probe.uc (config()): the mode, and in local mode the
	# only interfaces slots may attach to
	{
		echo "# Written by contrib/labgrid-probe/install-probe.sh (openwrt-tests)"
		echo "config probe 'main'"
		if [ "$kind" = local ]; then
			echo "	option mode 'local'"
			for p in "$@"; do
				echo "	list parent '$p'"
			done
		else
			echo "	option mode 'trunk'"
		fi
	} >/etc/config/labgrid_probe
	;;
esac

if [ "$kind" = lan ] && [ -f /etc/uci-defaults/90-labgrid-probe ]; then
	# --config
	( cd /etc/uci-defaults && . ./90-labgrid-probe ) && rm -f /etc/uci-defaults/90-labgrid-probe
	/etc/init.d/firewall reload
	echo "probe config installed; the network config applies at the next reboot"
fi

if [ "$kind" = local ]; then
	for p in "$@"; do
		[ -e "/sys/class/net/$p" ] || echo "warning: --parent $p: no such interface here"
	done

	# A uhttpd instance of its own on loopback (the probe place's
	# NetworkService); the host's other uhttpd config (LuCI) stays as it is
	changed=""
	s=uhttpd.labgrid_probe
	set_opt() {
		[ "$(uci -q get "$s.$1")" = "$2" ] && return
		uci set "$s.$1=$2"
		changed=1
	}
	mkdir -p /usr/share/labgrid-probe/www
	[ "$(uci -q get $s)" = uhttpd ] || { uci set $s=uhttpd; changed=1; }
	if [ "$(uci -q get $s.listen_http)" != 127.0.0.1:8080 ]; then
		uci -q delete $s.listen_http || true
		uci add_list $s.listen_http=127.0.0.1:8080
		changed=1
	fi
	set_opt home /usr/share/labgrid-probe/www
	set_opt ubus_prefix /ubus
	set_opt rfc1918_filter 0
	if [ -n "$changed" ]; then
		uci commit uhttpd
		restart_uhttpd=1
	fi

	# The veths of the WAN namespaces (labgrid_probe.uc wan_up): DUT traffic
	# may only pass through to the uplink, not reach the host itself
	changed=""
	s=firewall.labgrid_probe
	[ "$(uci -q get $s)" = zone ] || { uci set $s=zone; changed=1; }
	set_opt name lgp_uplink
	set_opt device 'pbx+'
	set_opt input REJECT
	set_opt output ACCEPT
	set_opt forward REJECT
	s=firewall.labgrid_probe_fwd
	[ "$(uci -q get $s)" = forwarding ] || { uci set $s=forwarding; changed=1; }
	set_opt src lgp_uplink
	set_opt dest wan
	if [ -n "$changed" ]; then
		uci commit firewall
		/etc/init.d/firewall reload
		echo "firewall zone lgp_uplink (pbx+) forwarded to wan"
	fi
fi

if [ "$kind" = wifi ]; then
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
	if [ -f /etc/modules.d/ath9k ]; then
		grep -q 'nohwcrypt=1' /etc/modules.d/ath9k || echo 'ath9k nohwcrypt=1' >/etc/modules.d/ath9k
		[ "$(cat /sys/module/ath9k/parameters/nohwcrypt 2>/dev/null)" = 1 ] || reboot_needed=1
	fi
fi

# rpcd silently skips plugins that don't compile
for f in $compile; do
	if ! out=$(ucode -c -o /dev/null "$f" 2>&1) || [ -n "$out" ]; then
		echo "ucode compile failed: $f"
		echo "$out"
		exit 1
	fi
done

/etc/init.d/rpcd restart
[ -z "$restart_uhttpd" ] || /etc/init.d/uhttpd restart
sleep 2
ubus -S list "$object" >/dev/null 2>&1 || {
	echo "ubus object $object missing; run rpcd in the foreground to see why:"
	echo "  /etc/init.d/rpcd stop; rpcd; /etc/init.d/rpcd start"
	exit 1
}
ubus call session access "{\"ubus_rpc_session\":\"00000000000000000000000000000000\",\"scope\":\"ubus\",\"object\":\"$object\",\"function\":\"$check\"}" \
	| grep -q '"access": true' || {
	echo "the unauthenticated session may not call $object: check /usr/share/rpcd/acl.d/$object.json"
	exit 1
}
pidof uhttpd >/dev/null || {
	echo "uhttpd is not running"
	exit 1
}
if [ "$kind" != local ]; then
	uci -q get uhttpd.main.ubus_prefix >/dev/null || echo "warning: uhttpd has no ubus_prefix (/ubus): tests can't reach the probe"
fi

case "$kind" in
lan)
	# Without lan_vlan: a needs_vlan reply (an outdated probe: ubus status 2)
	ubus call labgrid-probe up '{}'
	;;
local)
	ubus call labgrid-probe info '{"lan_vlan":1}'
	;;
wifi)
	ubus call labgrid-wifi-probe radios
	;;
esac

if [ -n "$reboot_needed" ]; then
	echo "rebooting for ath9k nohwcrypt=1 (software crypto)"
	reboot
fi
EOF
