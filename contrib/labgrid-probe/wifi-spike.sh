#!/bin/sh
# Wi-Fi probe spike: many client stations on one ath9k radio against one AP.
#
# A measurement, not part of the probe (Ansible doesn't install it). Copy it
# to a TL-WDR4300 running OpenWrt and run it there by hand:
#
#   scp -O wifi-spike.sh root@<probe>:/tmp/
#   ssh root@<probe> sh /tmp/wifi-spike.sh up 2412 32 OpenWrt-CI-2g <dut bssid> psk2 openwrt4all
#   ssh root@<probe> sh /tmp/wifi-spike.sh status
#   ssh root@<probe> sh /tmp/wifi-spike.sh down
#
#   up <freq MHz> <count> <ssid> <bssid> <none|psk2|sae> [key]
#
# What it does, per station: a managed interface ws<phy>s<i> on the radio
# for <freq>, with a locally administered MAC that differs from the radio's
# only in its last two bytes (keeps ath9k's BSSID mask narrow); a
# wpa_supplicant locked to <bssid> and <freq>; a DHCP lease from the AP
# (udhcpc); the lease as a /32 with a policy routing table of its own (all
# stations share the root namespace: cfg80211 interfaces can't move into
# another netns one by one); a ping to the DHCP server through that station.
#
# It prints the time to associate and to get leases, MemAvailable at each
# step, the wpa_supplicant RSS, and how many keys mac80211 holds. Run with
# 8, 32 and 64 stations, with psk2 and sae, and compare.
#
# Before `up`: the radio must be free (no netifd interfaces on it):
#   uci set wireless.radio0.disabled=1; uci set wireless.radio1.disabled=1
#   uci commit wireless; wifi
# Needs wpad-basic-mbedtls (wpa_supplicant with SAE) and ip-full (ip rule).
#
# Environment: BATCH (stations per wpa_supplicant process, 8), STAGGER
# (seconds between batches, 2), ASSOC_TIMEOUT (120), DHCP_TIMEOUT (60),
# COUNTRY (regulatory domain, DE).

set -u

STATE=/tmp/wifi-spike
BATCH=${BATCH:-8}
STAGGER=${STAGGER:-2}
ASSOC_TIMEOUT=${ASSOC_TIMEOUT:-120}
DHCP_TIMEOUT=${DHCP_TIMEOUT:-60}
COUNTRY=${COUNTRY:-DE}
# Policy routing tables (and rule preferences): TABLE_BASE + phy * 4096 + i
TABLE_BASE=10000

die() {
	echo "error: $*" >&2
	exit 1
}

now() { cut -d' ' -f1 /proc/uptime; }
since() { awk -v a="$1" -v b="$(now)" 'BEGIN { printf "%.1f", b - a }'; }
mem_avail() { awk '/^MemAvailable:/ { print $2 }' /proc/meminfo; }

# The phy that has <freq> enabled
find_phy() {
	local p
	for p in /sys/class/ieee80211/*; do
		[ -e "$p" ] || continue
		if iw phy "${p##*/}" info | grep -E "\* $1(\.0)? MHz" | grep -qv disabled; then
			echo "${p##*/}"
			return 0
		fi
	done
	return 1
}

# MAC of station <i> on <phy>: the radio's address, locally administered,
# byte 4 = phy number * 16 + i / 256, byte 5 = i % 256
mac_for() {
	local phy=$1 i=$2 n=${1#phy}
	local IFS=:
	set -- $(cat "/sys/class/ieee80211/$phy/macaddress")
	printf '%02x:%s:%s:%s:%02x:%02x\n' $((0x$1 | 2)) "$2" "$3" "$4" \
		$((n * 16 + i / 256)) $((i % 256))
}

# Number of connected stations of <phy>: operstate is "up" once
# wpa_supplicant has the keys (dormant while associating, down without a
# link). `iw dev` shows no ssid for stations on every kernel.
connected() {
	cat /sys/class/net/ws"${1#phy}"s*/operstate 2>/dev/null | grep -c '^up$'
}

supplicant_rss() {
	local f pid total=0 rss
	for f in "$1"/wpas-*.pid; do
		[ -e "$f" ] || continue
		pid=$(cat "$f")
		rss=$(awk '/^VmRSS:/ { print $2 }' "/proc/$pid/status" 2>/dev/null)
		total=$((total + ${rss:-0}))
	done
	echo "$total"
}

keys_info() {
	local d=/sys/kernel/debug/ieee80211/$1/keys
	if [ -d "$d" ]; then
		echo "mac80211 keys on $1: $(ls "$d" | wc -l)"
		echo "hw_key_idx (count value):"
		cat "$d"/*/hw_key_idx 2>/dev/null | sort | uniq -c | sort -rn | head -n 10
	else
		echo "no $d (debugfs not mounted or MAC80211_DEBUGFS off)"
	fi
}

write_conf() {
	# <file> <ssid> <bssid> <freq> <encryption> <key>
	{
		echo "network={"
		echo "	ssid=\"$2\""
		echo "	bssid=$3"
		echo "	scan_freq=$4"
		echo "	freq_list=$4"
		case "$5" in
		none) echo "	key_mgmt=NONE" ;;
		psk2)
			echo "	key_mgmt=WPA-PSK"
			echo "	proto=RSN"
			echo "	pairwise=CCMP"
			echo "	psk=\"$6\""
			;;
		sae)
			echo "	key_mgmt=SAE"
			echo "	ieee80211w=2"
			echo "	psk=\"$6\""
			;;
		esac
		echo "}"
	} >"$1"
}

write_dhcp_script() {
	# udhcpc hook: only records the lease, configures nothing
	cat >"$STATE/udhcpc.sh" <<'EOF'
#!/bin/sh
case "$1" in
bound|renew)
	echo "${ip} ${router%% *} ${serverid}" >"$LEASE_DIR/lease-$interface"
	;;
esac
exit 0
EOF
	chmod +x "$STATE/udhcpc.sh"
}

up() {
	[ $# -ge 5 ] || die "usage: $0 up <freq MHz> <count> <ssid> <bssid> <none|psk2|sae> [key]"
	local freq=$1 count=$2 ssid=$3 bssid=$4 enc=$5 key=${6:-}
	case "$enc" in
	none) ;;
	psk2 | sae) [ -n "$key" ] || die "$enc needs a key" ;;
	*) die "encryption: none, psk2 or sae" ;;
	esac
	[ "$count" -ge 1 ] && [ "$count" -le 4096 ] || die "count: 1..4096"
	echo "$bssid" | grep -qiE '^([0-9a-f]{2}:){5}[0-9a-f]{2}$' ||
		die "bssid '$bssid' is not a MAC address (the DUT AP's 'addr' in 'iw dev')"
	command -v wpa_supplicant >/dev/null || die "no wpa_supplicant (wpad-basic-mbedtls)"
	[ -x /usr/libexec/ip-full ] || die "no ip-full (policy routing): apk add ip-full"

	local phy n dir
	phy=$(find_phy "$freq") || die "no radio with $freq MHz enabled"
	n=${phy#phy}
	dir=$STATE/$phy
	[ -d "$dir" ] && die "$phy is already up: run '$0 down' first"
	if [ -n "$(ls "/sys/class/ieee80211/$phy/device/net/" 2>/dev/null)" ]; then
		die "$phy has interfaces ($(ls "/sys/class/ieee80211/$phy/device/net/" | tr '\n' ' ')): disable its radio in /etc/config/wireless and run 'wifi'"
	fi

	mkdir -p "$dir"
	write_dhcp_script
	iw reg set "$COUNTRY"
	echo 1 >/proc/sys/net/ipv4/conf/all/arp_ignore
	echo 2 >/proc/sys/net/ipv4/conf/all/arp_announce
	echo 0 >/proc/sys/net/ipv4/conf/all/rp_filter

	local mem0 t0 i ifn mac
	mem0=$(mem_avail)
	t0=$(now)
	echo "$phy ($freq MHz), $count stations, $enc, AP $bssid ($ssid)"
	echo "MemAvailable before: $mem0 kB"

	for i in $(seq 0 $((count - 1))); do
		ifn=ws${n}s$i
		mac=$(mac_for "$phy" "$i")
		iw phy "$phy" interface add "$ifn" type managed || die "creating $ifn failed after $i stations"
		echo "$ifn" >>"$dir/ifaces"
		ip link set "$ifn" address "$mac" || die "setting $mac on $ifn failed"
		echo 0 >"/proc/sys/net/ipv4/conf/$ifn/rp_filter"
		echo 1 >"/proc/sys/net/ipv6/conf/$ifn/disable_ipv6"
		ip link set "$ifn" up || die "$ifn doesn't come up"
		iw dev "$ifn" set power_save off 2>/dev/null
		write_conf "$dir/$ifn.conf" "$ssid" "$bssid" "$freq" "$enc" "$key"
	done
	local mem1
	mem1=$(mem_avail)
	echo "interfaces: $count in $(since "$t0") s, MemAvailable $mem1 kB ($((mem0 - mem1)) kB used)"

	# wpa_supplicant: one process per BATCH stations, STAGGER s apart
	# (the count is checked between batches too, so "first" is measured
	# while later batches still start)
	local t1 b=0 args="" in_batch=0 c=0 first=""
	t1=$(now)
	for ifn in $(cat "$dir/ifaces"); do
		args="$args -i $ifn -c $dir/$ifn.conf -N"
		in_batch=$((in_batch + 1))
		if [ "$in_batch" -eq "$BATCH" ]; then
			wpa_supplicant -B -P "$dir/wpas-$b.pid" -D nl80211 ${args% -N} || die "wpa_supplicant batch $b failed"
			echo "batch $b started after $(since "$t1") s"
			b=$((b + 1)); args=""; in_batch=0
			sleep "$STAGGER"
			[ -z "$first" ] && [ "$(connected "$phy")" -gt 0 ] && first=$(since "$t1")
		fi
	done
	if [ -n "$args" ]; then
		wpa_supplicant -B -P "$dir/wpas-$b.pid" -D nl80211 ${args% -N} || die "wpa_supplicant batch $b failed"
		echo "batch $b started after $(since "$t1") s"
		b=$((b + 1))
	fi

	while :; do
		c=$(connected "$phy")
		[ -z "$first" ] && [ "$c" -gt 0 ] && first=$(since "$t1")
		[ "$c" -ge "$count" ] && break
		[ "$(awk -v a="$t1" -v b="$(now)" -v t="$ASSOC_TIMEOUT" 'BEGIN { print (b - a > t) }')" = 1 ] && break
		sleep 1
	done
	local mem2
	mem2=$(mem_avail)
	echo "associated: $c/$count, first after ${first:--} s, last after $(since "$t1") s"
	echo "MemAvailable $mem2 kB, wpa_supplicant RSS $(supplicant_rss "$dir") kB ($b processes)"

	# DHCP on the connected stations, all at once
	local t2
	t2=$(now)
	for ifn in $(cat "$dir/ifaces"); do
		iw dev "$ifn" link | grep -q '^Connected' || continue
		LEASE_DIR=$dir udhcpc -f -q -n -t 10 -T 3 -i "$ifn" \
			-x "hostname:wprobe-$n-${ifn##*s}" -s "$STATE/udhcpc.sh" >/dev/null 2>&1 &
		echo $! >>"$dir/udhcpc.pids"
	done
	local leases=0
	while :; do
		leases=$(ls "$dir"/lease-* 2>/dev/null | wc -l)
		[ "$leases" -ge "$c" ] && break
		[ "$(awk -v a="$t2" -v b="$(now)" -v t="$DHCP_TIMEOUT" 'BEGIN { print (b - a > t) }')" = 1 ] && break
		sleep 1
	done
	echo "leases: $leases/$c after $(since "$t2") s"

	# Per station: /32, own table, rule by source address; ping the server
	local f ipaddr router server table
	for f in "$dir"/lease-*; do
		[ -e "$f" ] || continue
		ifn=${f##*/lease-}
		read -r ipaddr router server <"$f"
		router=${router:-$server}
		i=${ifn##*s}
		table=$((TABLE_BASE + n * 4096 + i))
		ip addr add "$ipaddr/32" dev "$ifn"
		ip route add "$router" dev "$ifn" scope link table "$table"
		ip route add default via "$router" dev "$ifn" table "$table"
		ip rule add from "$ipaddr" lookup "$table" pref "$table"
		echo "$table" >>"$dir/tables"
		(ping -c 3 -W 2 -I "$ipaddr" "$router" >"$dir/ping-$ifn" 2>&1; echo "rc=$?" >>"$dir/ping-$ifn") &
	done
	wait
	local ok
	ok=$(grep -l '^rc=0' "$dir"/ping-* 2>/dev/null | wc -l)
	echo "ping to the DHCP server: $ok/$leases stations"
	echo "MemAvailable at the end: $(mem_avail) kB ($((mem0 - $(mem_avail))) kB used in total)"
	keys_info "$phy"
	echo "kernel messages:"
	dmesg | grep -iE 'ath9k|ath:|ieee80211|keycache' | tail -n 20
}

status() {
	local dir phy ifn
	for dir in "$STATE"/phy*; do
		[ -d "$dir" ] || { echo "nothing up"; return 0; }
		phy=${dir##*/}
		echo "== $phy: $(connected "$phy")/$(wc -l <"$dir/ifaces") connected, MemAvailable $(mem_avail) kB, wpa_supplicant RSS $(supplicant_rss "$dir") kB"
		for ifn in $(cat "$dir/ifaces"); do
			printf '%s %s %s\n' "$ifn" \
				"$(iw dev "$ifn" link | awk '/^Connected/ { print "assoc" } /^Not connected/ { print "-" }')" \
				"$(cut -d' ' -f1 "$dir/lease-$ifn" 2>/dev/null)"
		done
		keys_info "$phy"
	done
}

down() {
	local dir f table ifn
	for dir in "$STATE"/phy*; do
		[ -d "$dir" ] || continue
		for f in "$dir"/wpas-*.pid "$dir/udhcpc.pids"; do
			[ -e "$f" ] && kill $(cat "$f") 2>/dev/null
		done
		if [ -e "$dir/tables" ]; then
			for table in $(cat "$dir/tables"); do
				ip rule del pref "$table" 2>/dev/null
				ip route flush table "$table" 2>/dev/null
			done
		fi
		if [ -e "$dir/ifaces" ]; then
			for ifn in $(cat "$dir/ifaces"); do
				iw dev "$ifn" del 2>/dev/null
			done
		fi
		echo "${dir##*/} down"
	done
	rm -rf "$STATE"
}

cmd=${1:-}
[ $# -gt 0 ] && shift
case "$cmd" in
up) up "$@" ;;
status) status ;;
down) down ;;
*) die "usage: $0 up <freq MHz> <count> <ssid> <bssid> <none|psk2|sae> [key] | status | down" ;;
esac
