#!/bin/sh
# Copy the labgrid-probe files (contrib/labgrid-probe/files) to an OpenWrt device
# and activate them.
#
# usage: deploy.sh [-c] [-J jumphost] [-k pubkey] [-p] [-r] [root@]<probe>
#
#   (default)    copy only the code (files/usr), compile-check it and restart
#                rpcd. Leaves the probe's network/firewall config alone.
#   -c           also copy the config (files/etc: network, firewall, system,
#                uci-defaults) and apply uci-defaults. For first installs or
#                deliberate config changes: it replaces local tweaks, and the
#                new network config takes effect on the next reboot (-r).
#   -J jumphost  reach the probe through this host, e.g. the lab host
#                (labgrid-aparcar). Default: $LG_PROXY if set, otherwise direct.
#   -k pubkey    add this public key (the lab host user's) to the probe's
#                /etc/dropbear/authorized_keys, so it can log in without a password.
#   -p           install the required packages first (apk, or opkg on <= 24.10);
#                the probe needs internet access for this
#   -r           reboot afterwards
#
# Examples:
#   # fresh device (factory 192.168.1.1), packages already in the image:
#   ./deploy.sh -c -k labhost.pub -r 192.168.1.1
#   # update the code later:
#   ./deploy.sh 192.168.128.3
#
# With the shipped config, the probe answers on 192.168.128.3 (eth0, lab
# management) and on 192.168.1.1 with DHCP on its 1G LAN port (eth1).

set -eu

PACKAGES="ip-full iperf3 ucode ucode-mod-fs ucode-mod-uci rpcd rpcd-mod-ucode uhttpd uhttpd-mod-ubus"
FILES_DIR="$(cd "$(dirname "$0")" && pwd)/files"

jump="${LG_PROXY:-}"
config=0
pubkey=""
install=0
reboot=0

usage() {
	sed -n '2,28s/^# \{0,1\}//p' "$0" >&2
	exit 64
}

while getopts "cJ:k:prh" opt; do
	case "$opt" in
	c) config=1 ;;
	J) jump="$OPTARG" ;;
	k) pubkey="$OPTARG" ;;
	p) install=1 ;;
	r) reboot=1 ;;
	*) usage ;;
	esac
done
shift $((OPTIND - 1))
[ $# -eq 1 ] || usage

target="$1"
case "$target" in
*@*) ;;
*) target="root@$target" ;;
esac

# Keepalives: if a step cuts the connection, fail after ~15 s instead of
# hanging. OpenWrt's dropbear has no sftp-server, so scp needs -O.
ssh_opts="-o StrictHostKeyChecking=accept-new -o ServerAliveInterval=5 -o ServerAliveCountMax=3"
[ -n "$jump" ] && ssh_opts="$ssh_opts -J $jump"

remote() {
	# shellcheck disable=SC2086
	ssh $ssh_opts "$target" "$@"
}

[ -d "$FILES_DIR" ] || { echo "missing $FILES_DIR" >&2; exit 1; }
[ -z "$pubkey" ] || [ -r "$pubkey" ] || { echo "can't read $pubkey" >&2; exit 1; }

echo ">> $target${jump:+ (via $jump)}"

if [ "$install" = 1 ]; then
	echo ">> installing: $PACKAGES"
	remote "if command -v apk >/dev/null; then apk update && apk add $PACKAGES; \
		else opkg update && opkg install $PACKAGES; fi"
fi

if [ -n "$pubkey" ]; then
	echo ">> adding $pubkey to authorized_keys"
	remote 'umask 077; k=/etc/dropbear/authorized_keys; touch $k; \
		while read -r line; do grep -qxF "$line" $k || echo "$line" >> $k; done' < "$pubkey"
fi

# -p keeps the modes (0755 scripts, 0644 plugin: rpcd skips world-writable files)
echo ">> copying code"
# shellcheck disable=SC2086
scp -O -p -r $ssh_opts "$FILES_DIR/usr" "$target:/"

if [ "$config" = 1 ]; then
	echo ">> copying config"
	# shellcheck disable=SC2086
	scp -O -p -r $ssh_opts "$FILES_DIR/etc" "$target:/"
	echo ">> applying uci-defaults"
	remote 'set -e
		if [ -f /etc/uci-defaults/90-labgrid-probe ]; then
			( cd /etc/uci-defaults && . ./90-labgrid-probe ) && rm -f /etc/uci-defaults/90-labgrid-probe
		fi
		echo "   firewall reload"
		/etc/init.d/firewall reload >/dev/null 2>&1 || true'
fi

echo ">> activating"
remote 'set -e
	# Earlier name of the probe (lanclient): drop its plugin, module and hook
	rm -f /usr/share/ucode/lanclient.uc /usr/share/rpcd/ucode/lanclient.uc \
		/usr/share/rpcd/acl.d/lanclient.json /usr/bin/lanclient /usr/share/udhcpc/lanclient.script
	chown root:root /usr/share/ucode/labgrid_probe.uc /usr/share/rpcd/ucode/labgrid-probe.uc \
		/usr/share/rpcd/acl.d/labgrid-probe.json /usr/share/udhcpc/labgrid-probe.script
	# rpcd silently skips plugins that fail to compile (its stderr goes nowhere),
	# so compile here first and show the error. This also pulls in the shared
	# module via its import.
	for f in /usr/share/rpcd/ucode/labgrid-probe.uc /usr/share/udhcpc/labgrid-probe.script; do
		if ! out="$(ucode -c -o /dev/null "$f" 2>&1)" || [ -n "$out" ]; then
			echo "ucode compile failed: $f" >&2
			echo "$out" >&2
			exit 1
		fi
	done
	echo "   ucode compile: ok"
	echo "   rpcd restart"
	/etc/init.d/rpcd restart
	sleep 1
	if ubus -S list labgrid-probe >/dev/null 2>&1; then
		echo "   ubus object labgrid-probe: ok"
	else
		echo "ubus object labgrid-probe missing; run rpcd in the foreground to see why:" >&2
		echo "  /etc/init.d/rpcd stop; rpcd; /etc/init.d/rpcd start" >&2
		exit 1
	fi
	# Tests call it over HTTP with the unauthenticated session (rpcd ACL)
	if ubus call session access "{\"ubus_rpc_session\":\"00000000000000000000000000000000\",\"scope\":\"ubus\",\"object\":\"labgrid-probe\",\"function\":\"info\"}" | grep -q "\"access\": true"; then
		echo "   HTTP access (unauthenticated session): ok"
	else
		echo "the unauthenticated session may not call labgrid-probe: check /usr/share/rpcd/acl.d/labgrid-probe.json" >&2
		exit 1
	fi
	if pidof uhttpd >/dev/null; then
		echo "   uhttpd: running"
	else
		echo "uhttpd is not running: install uhttpd uhttpd-mod-ubus (-p) and start it" >&2
		exit 1
	fi'

if [ "$reboot" = 1 ]; then
	echo ">> rebooting"
	remote reboot || true
fi

echo ">> done"
