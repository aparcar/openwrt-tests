#!/bin/sh
# Set up a lab's PoE switch running OpenWrt (DSA) over SSH, from the
# operator's machine: one VLAN per DUT port, the trunks to the lab host, the
# probe and other switches, and the management address. It also checks what
# pdudaemon's ubus driver needs to switch PoE (uhttpd with /ubus, rpcd, the
# `poe` ubus object) and installs the rpcd ACL that lets the driver's session
# call it (files/usr/share/rpcd/acl.d/pdudaemon.json): the driver logs in as
# root, but root only gets what some ACL file grants, and none covers `poe`.
# realtek-poe has that object; on a switch with poemgr instead (Plasma Cloud
# PSX, UniFi Flex) the script installs an rpcd plugin providing it
# (files/usr/share/rpcd/ucode/poe.uc).
#
#   sh contrib/labgrid-switch/install-switch.sh --name labgrid-switch-1 \
#       --address 172.20.0.2/24 --gateway 172.20.0.1 \
#       --first-vlan 101 --ports 24 --transit 125-132 \
#       --trunk lan25 --trunk lan26 --trunk lan28 --mgmt lan27 \
#       root@<switch> -J labgrid-aparcar
#
#   sh contrib/labgrid-switch/install-switch.sh --name labgrid-switch-2 \
#       --address 172.20.0.6/24 --gateway 172.20.0.1 \
#       --first-vlan 125 --ports 8 --trunk lan9 \
#       root@<switch> -J labgrid-aparcar
#
#   --first-vlan <id>   VLAN of lan1; lan<N> is the untagged member of VLAN
#                       <id> + N - 1
#   --ports <count>     number of DUT ports (lan1..lan<count>)
#   --trunk <port>      carries every DUT and transit VLAN tagged and the
#                       management network untagged: the lab host, the probe,
#                       the link to another switch. At least one.
#   --mgmt <port>       management network only (untagged)
#   --transit <a>-<b>   VLANs of another switch's DUT ports, passed between
#                       the trunks (tagged) without a port of their own here
#   --address <ip/len>  management address of the switch
#   --gateway <ip>      the lab host; also sets DNS 9.9.9.9 (the lab host
#                       serves none), reached through the lab host's NAT
#   --name <hostname>   hostname of the switch, e.g. its name in the lab
#                       host's /etc/hosts (labgrid_hosts) and pdudaemon.conf
#   --mgmt-vlan <id>    VLAN of the management network inside the switch
#                       (default 1; untagged on the wire either way)
#
# Ports that aren't named are taken out of the bridge. The network config is
# written and committed but NOT applied: the switch keeps its current address
# and VLANs until `/etc/init.d/network restart` or a reboot (a reboot power
# cycles every PoE device), so check the printed summary first. Run it again
# after changing the layout. Arguments after the destination go to ssh.
set -eu

usage() {
	echo "usage: $0 --address <ip/len> --first-vlan <id> --ports <count> --trunk <port>... [--mgmt <port>...] [--transit <a>-<b>] [--gateway <ip>] [--name <hostname>] [--mgmt-vlan <id>] <user@host> [ssh options]" >&2
	exit 2
}

name=""
address=""
gateway=""
first_vlan=""
ports=""
transit=""
trunks=""
mgmts=""
mgmt_vlan=1
while [ $# -gt 0 ]; do
	case "$1" in
	--name | --address | --gateway | --first-vlan | --ports | --transit | --trunk | --mgmt | --mgmt-vlan)
		[ $# -ge 2 ] || usage
		case "$1" in
		--name) name=$2 ;;
		--address) address=$2 ;;
		--gateway) gateway=$2 ;;
		--first-vlan) first_vlan=$2 ;;
		--ports) ports=$2 ;;
		--transit) transit=$2 ;;
		--trunk) trunks="${trunks:+$trunks,}$2" ;;
		--mgmt) mgmts="${mgmts:+$mgmts,}$2" ;;
		--mgmt-vlan) mgmt_vlan=$2 ;;
		esac
		shift 2
		;;
	-*)
		usage
		;;
	*)
		break
		;;
	esac
done
[ $# -ge 1 ] || usage
[ -n "$address" ] && [ -n "$first_vlan" ] && [ -n "$ports" ] && [ -n "$trunks" ] || usage
dest=$1
shift
files=$(dirname "$0")/files

# Every switch gets the ACL for pdudaemon's session. A switch whose PoE is
# driven by poemgr (Plasma Cloud PSX, UniFi Flex) also gets the rpcd plugin
# offering the `poe` ubus object pdudaemon talks to. Not on switches with
# realtek-poe, which has that object itself.
# COPYFILE_DISABLE: no macOS ._ metadata files in the archive. -o (busybox
# tar): the files belong to root, not to the operator's uid
copy=usr/share/rpcd/acl.d/pdudaemon.json
if ssh "$@" "$dest" '[ -x /sbin/poemgr ]'; then
	copy="$copy usr/share/rpcd/ucode/poe.uc"
fi
# shellcheck disable=SC2086 # copy is a list
COPYFILE_DISABLE=1 tar -C "$files" -cf - $copy |
	ssh "$@" "$dest" 'tar -C / -oxf -'

# ssh joins its arguments with spaces and the remote shell splits them again:
# lists are comma separated and no value may contain a space
ssh "$@" "$dest" sh -s -- "name=$name" "address=$address" "gateway=$gateway" \
	"first_vlan=$first_vlan" "ports=$ports" "transit=$transit" \
	"trunks=$trunks" "mgmts=$mgmts" "mgmt_vlan=$mgmt_vlan" <<'EOF'
set -e
for a in "$@"; do
	case "$a" in
	name=*) name=${a#*=} ;;
	address=*) address=${a#*=} ;;
	gateway=*) gateway=${a#*=} ;;
	first_vlan=*) first_vlan=${a#*=} ;;
	ports=*) ports=${a#*=} ;;
	transit=*) transit=${a#*=} ;;
	trunks=*) trunks=$(echo "${a#*=}" | tr ',' ' ') ;;
	mgmts=*) mgmts=$(echo "${a#*=}" | tr ',' ' ') ;;
	mgmt_vlan=*) mgmt_vlan=${a#*=} ;;
	esac
done

fail() {
	echo "error: $*" >&2
	exit 1
}

is_vlan() {
	case "$1" in '' | *[!0-9]*) return 1 ;; esac
	[ "$1" -ge 1 ] && [ "$1" -le 4094 ]
}

# --- Check everything before changing anything ------------------------------

is_vlan "$first_vlan" || fail "--first-vlan $first_vlan: not a VLAN id"
is_vlan "$mgmt_vlan" || fail "--mgmt-vlan $mgmt_vlan: not a VLAN id"
case "$ports" in '' | *[!0-9]*) fail "--ports $ports: not a number" ;; esac
[ "$ports" -ge 1 ] || fail "--ports must be at least 1"
last_vlan=$((first_vlan + ports - 1))
is_vlan "$last_vlan" || fail "the last DUT VLAN would be $last_vlan"
case "$address" in */*) ;; *) fail "--address $address: prefix length missing (e.g. /24)" ;; esac

transit_first=""
transit_last=""
if [ -n "$transit" ]; then
	transit_first=${transit%-*}
	transit_last=${transit#*-}
	is_vlan "$transit_first" && is_vlan "$transit_last" && [ "$transit_first" -le "$transit_last" ] ||
		fail "--transit $transit: expected <first>-<last>"
	[ "$transit_last" -lt "$first_vlan" ] || [ "$transit_first" -gt "$last_vlan" ] ||
		fail "--transit $transit overlaps the DUT VLANs $first_vlan-$last_vlan"
fi

used() {
	[ "$1" -ge "$first_vlan" ] && [ "$1" -le "$last_vlan" ] && return 0
	[ -n "$transit" ] && [ "$1" -ge "$transit_first" ] && [ "$1" -le "$transit_last" ]
}
if used "$mgmt_vlan"; then
	fail "--mgmt-vlan $mgmt_vlan is one of the DUT or transit VLANs"
fi

dut_ports=""
i=1
while [ "$i" -le "$ports" ]; do
	dut_ports="$dut_ports lan$i"
	i=$((i + 1))
done
for p in $dut_ports $trunks $mgmts; do
	[ -e "/sys/class/net/$p" ] || fail "no port $p on this switch"
done
for p in $trunks $mgmts; do
	case " $dut_ports " in *" $p "*) fail "$p is a DUT port (lan1..lan$ports) and can't be a trunk or management port" ;; esac
done
for p in $trunks; do
	case " $mgmts " in *" $p "*) fail "$p is both --trunk and --mgmt" ;; esac
done

bridge_sec=""
for s in $(uci -q show network | sed -n 's/^network\.\([^.=]*\)=device$/\1/p'); do
	if [ "$(uci -q get "network.$s.type")" = bridge ]; then
		bridge_sec=$s
		break
	fi
done
[ -n "$bridge_sec" ] || fail "no bridge device in /etc/config/network: is this an OpenWrt DSA switch?"
bridge=$(uci -q get "network.$bridge_sec.name")
[ "$(uci -q get network.lan)" = interface ] || fail "no interface 'lan' in /etc/config/network"

# --- Network ----------------------------------------------------------------

uci -q delete "network.$bridge_sec.ports" || true
for p in $dut_ports $trunks $mgmts; do
	uci add_list "network.$bridge_sec.ports=$p"
done

while uci -q delete "network.@bridge-vlan[0]"; do :; done

# Management: untagged (and PVID) on the trunks and the management ports
uci set network.vlan_mgmt=bridge-vlan
uci set "network.vlan_mgmt.device=$bridge"
uci set "network.vlan_mgmt.vlan=$mgmt_vlan"
for p in $trunks; do
	uci add_list "network.vlan_mgmt.ports=$p:u*"
done
for p in $mgmts; do
	uci add_list "network.vlan_mgmt.ports=$p:u*"
done

# One VLAN per DUT port, tagged on the trunks
add_vlan() {
	uci set "network.vlan_$1=bridge-vlan"
	uci set "network.vlan_$1.device=$bridge"
	uci set "network.vlan_$1.vlan=$1"
	[ -z "$2" ] || uci add_list "network.vlan_$1.ports=$2:u*"
	for t in $trunks; do
		uci add_list "network.vlan_$1.ports=$t:t"
	done
}
i=1
while [ "$i" -le "$ports" ]; do
	add_vlan "$((first_vlan + i - 1))" "lan$i"
	i=$((i + 1))
done
if [ -n "$transit" ]; then
	v=$transit_first
	while [ "$v" -le "$transit_last" ]; do
		add_vlan "$v" ""
		v=$((v + 1))
	done
fi

uci set "network.lan.device=$bridge.$mgmt_vlan"
uci set network.lan.proto=static
uci -q delete network.lan.ipaddr || true
uci -q delete network.lan.netmask || true
uci add_list "network.lan.ipaddr=$address"
uci -q delete network.lan.gateway || true
uci -q delete network.lan.dns || true
if [ -n "$gateway" ]; then
	uci set "network.lan.gateway=$gateway"
	uci add_list network.lan.dns=9.9.9.9
fi
uci commit network

if [ -n "$name" ]; then
	uci set "system.@system[0].hostname=$name"
	uci commit system
	echo "$name" >/proc/sys/kernel/hostname
fi

# --- PoE control for pdudaemon (ubus driver: HTTP /ubus, poe info/manage) ---
# The driver logs in (rpcd session.login, root by default). Its session may
# call `poe` through the ACL copied above; rpcd reads ACL files at login.

warn=""
[ -f /usr/share/rpcd/acl.d/pdudaemon.json ] ||
	warn="$warn\n- /usr/share/rpcd/acl.d/pdudaemon.json is missing: pdudaemon's session gets 'Access denied' for poe"

# poemgr switches: the `poe` object comes from our rpcd plugin (copied above)
plugin=/usr/share/rpcd/ucode/poe.uc
if [ -x /sbin/poemgr ] && [ -f "$plugin" ]; then
	pkgs="ucode ucode-mod-fs ucode-mod-uci rpcd rpcd-mod-ucode uhttpd uhttpd-mod-ubus"
	missing=""
	if command -v apk >/dev/null; then
		for p in $pkgs; do
			apk info -e "$p" >/dev/null 2>&1 || missing="$missing $p"
		done
		if [ -n "$missing" ]; then
			apk update
			# shellcheck disable=SC2086 # missing is a list
			apk add $missing
		fi
	else
		for p in $pkgs; do
			opkg list-installed "$p" | grep -q . || missing="$missing $p"
		done
		if [ -n "$missing" ]; then
			opkg update
			# shellcheck disable=SC2086 # missing is a list
			opkg install $missing
		fi
	fi

	# rpcd silently skips plugins that don't compile
	if ! out=$(ucode -c -o /dev/null "$plugin" 2>&1) || [ -n "$out" ]; then
		echo "ucode compile failed: $plugin"
		echo "$out"
		exit 1
	fi
	# Seen on a PSX10: the generated /etc/config/poemgr had `port '0'` in
	# every port section, so poemgr only ever configured the first PSE port
	# and every `poe manage` call hit that one. Number them in section order
	# (the order of the board's PoE port list) when they aren't distinct.
	poe_ports=$(uci -q show poemgr | sed -n "s/^poemgr\.\([^.=]*\)=port$/\1/p")
	poe_count=$(echo "$poe_ports" | grep -c .)
	poe_distinct=$(for s in $poe_ports; do uci -q get "poemgr.$s.port"; done | sort -u | grep -c .)
	if [ "$poe_count" -gt 1 ] && [ "$poe_distinct" -lt "$poe_count" ]; then
		i=0
		for s in $poe_ports; do
			uci set "poemgr.$s.port=$i"
			i=$((i + 1))
		done
		uci commit poemgr
		warn="$warn\n- /etc/config/poemgr had duplicate port numbers: renumbered 0..$((i - 1)) in section order, check that each lanN switches the right socket"
	fi
	/etc/init.d/poemgr enable
	/etc/init.d/poemgr start
	/etc/init.d/rpcd restart
	sleep 2
fi

if ! pidof rpcd >/dev/null; then
	warn="$warn\n- rpcd is not running: pdudaemon can't log in"
fi
if ! pidof uhttpd >/dev/null; then
	warn="$warn\n- uhttpd is not running: pdudaemon can't reach the switch (install uhttpd, uhttpd-mod-ubus)"
elif ! uci -q get uhttpd.main.ubus_prefix >/dev/null; then
	warn="$warn\n- uhttpd has no ubus_prefix (/ubus): install uhttpd-mod-ubus"
fi
if ! ubus -S list poe >/dev/null 2>&1; then
	warn="$warn\n- no ubus object 'poe' (realtek-poe, or our rpcd plugin on poemgr switches): pdudaemon's ubus driver can't switch PoE on this switch"
else
	echo "PoE ports: $(ubus call poe info | jsonfilter -e '@.ports' 2>/dev/null || echo '?')"
fi

# --- Summary ----------------------------------------------------------------

echo "$(cat /tmp/sysinfo/model 2>/dev/null || echo switch)${name:+ ($name)}: config written, not applied yet"
echo "  management  $address on VLAN $mgmt_vlan${gateway:+, gateway $gateway}"
echo "  DUT ports   lan1..lan$ports = VLAN $first_vlan..$last_vlan (untagged)"
[ -z "$transit" ] || echo "  transit     VLAN $transit_first..$transit_last (trunks only)"
echo "  trunks      $(echo $trunks) (all VLANs tagged, management untagged)"
[ -z "$mgmts" ] || echo "  management  $(echo $mgmts) (untagged)"
[ -z "$warn" ] || printf 'warnings:%b\n' "$warn"
echo "apply with: /etc/init.d/network restart   (the switch then answers on ${address%/*} only)"
EOF
