// labgrid-probe: a test client behind a DUT and a server in front of it, for
// openwrt-tests (drivers/labgridprobedriver.py, tests/test_probe_lan.py).
//
// No per-DUT configuration: the caller names the VLANs. Each DUT gets a slot,
// keyed by its LAN VLAN, with two network namespaces on the trunk port:
//
//   up(lan_vlan)        pb-lan<N>: VLAN device pbl<N>, a LAN client of the
//                       DUT (DHCP, DNS, SLAAC, iperf3 client)
//   wan_up(wan_vlan)    pb-wan<N>: VLAN device pbw<N>, 192.168.<wan>.2 by
//                       default; iperf3 server and the DHCP server for the
//                       DUT's WAN port (.100-.200, router .1 = the lab host)
//
// Traffic between the namespaces can only take the path through the DUT.
// Slot state is in <RUN_DIR>/<lan_vlan>/slot.json. Every exported function
// returns an object; failures carry an `error` string.

'use strict';

import { popen, readfile, writefile, access, mkdir, unlink, error as fs_error } from 'fs';
import { cursor } from 'uci';

// Overrides for off-device tests only; rpcd runs with a clean environment.
const RUN_DIR = getenv('LABGRID_PROBE_RUN_DIR') ?? '/var/run/labgrid-probe';
const NETNS_DIR = getenv('LABGRID_PROBE_NETNS_DIR') ?? '/var/run/netns';
const DHCP_HOOK = getenv('LABGRID_PROBE_DHCP_HOOK') ?? '/usr/share/udhcpc/labgrid-probe.script';

const WAN_HOST = 2;   // 192.168.<wan>.2: not the lab host (.1), outside its DHCP range

// --- helpers (declared before use: ucode doesn't hoist) ---------------------

function shq(s) {
	return `'${replace('' + s, "'", "'\\''")}'`;
}

function is_uint(v) {
	return v != null && match('' + v, /^[0-9]+$/) != null;
}

function vlan_id(v) {
	if (!is_uint(v))
		return null;
	v = int(v);
	return (v >= 1 && v <= 4094) ? v : null;
}

// Run argv through /bin/sh with every argument quoted (popen() only takes
// argv arrays in recent ucode). Returns { rc, out }.
function run(argv) {
	let p = popen(join(' ', map(argv, shq)) + ' 2>&1');
	if (p == null)
		return { rc: -1, out: fs_error() };
	let out = p.read('all');
	return { rc: p.close(), out };
}

// Run commands in order; returns an error string or null.
function run_all(cmds) {
	for (let argv in cmds) {
		let r = run(argv);
		if (r.rc != 0)
			return `${join(' ', argv)}: ${trim(r.out)} (rc ${r.rc})`;
	}
	return null;
}

function ns_exists(ns) {
	return access(`${NETNS_DIR}/${ns}`) == true;
}

function ns_destroy(ns) {
	if (!ns_exists(ns))
		return;
	let pids = filter(split(trim(run(['ip', 'netns', 'pids', ns]).out), /[[:space:]]+/), x => length(x));
	if (length(pids))
		system(['kill', ...pids]);
	run(['ip', 'netns', 'del', ns]);
}

// The DUT VLANs ride on the port carrying the lab management network
// untagged. Default: the port the default route uses (the running state),
// else the "mgmt" interface's device from UCI, else eth0. Returns
// [device, where it came from].
function default_trunk() {
	let r = run(['ip', '-j', 'route', 'show', 'default']);
	try {
		let dev = json(r.out)?.[0]?.dev;
		if (r.rc == 0 && type(dev) == 'string' && length(dev))
			return [dev, 'default route'];
	}
	catch (ex) {
	}
	let dev = cursor().get('network', 'mgmt', 'device');
	if (type(dev) == 'string' && length(dev))
		return [dev, 'uci network.mgmt.device'];
	return ['eth0', 'fallback'];
}

// Locally administered MACs (02:50:42 = "PB"), unique per VLAN
function mac(prefix, vlan) {
	return sprintf('02:50:42:%02x:%02x:%02x', prefix, vlan >> 8, vlan & 255);
}

function paths(lan_vlan) {
	let base = `${RUN_DIR}/${lan_vlan}`;
	return {
		dir: base,
		slot: `${base}/slot.json`,
		lease: `${base}/lease.json`,
		server_pid: `${base}/iperf3-server.pid`,
		server_log: `${base}/iperf3-server.log`,
		wan_dhcp_pid: `${base}/wan-dhcp.pid`,
		wan_dhcp_leases: `${base}/wan-dhcp.leases`,
		speedtest_out: `${base}/speedtest.json`,
		speedtest_rc: `${base}/speedtest.rc`,
	};
}

function save(d) {
	if (writefile(paths(d.lan_vlan).slot, sprintf('%J\n', d)) == null)
		return `save slot: ${fs_error()}`;
	return null;
}

function need_lan(d) {
	return ns_exists(d.lan_ns) ? null : { error: `namespace ${d.lan_ns} missing, call up first` };
}

// Packet counters of the LAN VLAN device, or null.
function link_stats(d) {
	let r = run(['ip', '-n', d.lan_ns, '-j', '-s', 'link', 'show', 'dev', d.lan_dev]);
	if (r.rc != 0)
		return null;
	try {
		let s = json(r.out)?.[0]?.stats64;
		return s ? { rx_packets: s.rx?.packets, tx_packets: s.tx?.packets } : null;
	}
	catch (ex) {
		return null;
	}
}

// --- API ----------------------------------------------------------------------

// The slot set up by up() for this LAN VLAN, or null.
function load(lan_vlan) {
	lan_vlan = vlan_id(lan_vlan);
	let data = lan_vlan ? readfile(paths(lan_vlan).slot) : null;
	if (data == null)
		return null;
	try {
		return json(data);
	}
	catch (ex) {
		return null;
	}
}

function info(d) {
	let pid = readfile(paths(d.lan_vlan).server_pid);
	return {
		...d,
		lan_ns_up: ns_exists(d.lan_ns),
		wan_ns_up: d.wan_vlan != null && ns_exists(d.wan_ns),
		server_pid: pid != null ? int(trim(pid)) : null,
	};
}

// Remove everything for a LAN VLAN (also half-built slots).
function down(lan_vlan) {
	lan_vlan = vlan_id(lan_vlan);
	if (lan_vlan == null)
		return { error: 'lan_vlan must be 1..4094' };
	ns_destroy(`pb-lan${lan_vlan}`);
	ns_destroy(`pb-wan${lan_vlan}`);
	let p = paths(lan_vlan);
	for (let f in [p.slot, p.lease, p.server_pid, p.wan_dhcp_pid, p.wan_dhcp_leases,
	               p.speedtest_out, p.speedtest_rc])
		unlink(f);
	return { ok: true };
}

function up(lan_vlan, trunk) {
	lan_vlan = vlan_id(lan_vlan);
	let trunk_source = 'argument';
	if (trunk == null) {
		let found = default_trunk();   // ucode has no destructuring
		trunk = found[0];
		trunk_source = found[1];
	}
	if (lan_vlan == null)
		return { error: 'lan_vlan must be 1..4094' };
	if (type(trunk) != 'string' || !match(trunk, /^[A-Za-z0-9_.-]{1,15}$/))
		return { error: `bad trunk device name '${trunk}'` };

	down(lan_vlan);

	let d = {
		trunk,
		trunk_source,
		lan_vlan,
		lan_ns: `pb-lan${lan_vlan}`,
		lan_dev: `pbl${lan_vlan}`,
		lan_mac: mac(0, lan_vlan),
		hostname: `probe${lan_vlan}`,
		wan_vlan: null,
		wan_ip: null,
		wan_ns: `pb-wan${lan_vlan}`,
		wan_dev: `pbw${lan_vlan}`,
		wan_mac: null,
	};

	mkdir(RUN_DIR);
	mkdir(paths(lan_vlan).dir);

	let err = run_all([
		['ip', 'link', 'set', trunk, 'up'],
		['ip', 'netns', 'add', d.lan_ns],
		['ip', 'link', 'add', 'link', trunk, 'name', d.lan_dev, 'address', d.lan_mac,
			'type', 'vlan', 'id', lan_vlan],
		['ip', 'link', 'set', d.lan_dev, 'netns', d.lan_ns],
		['ip', '-n', d.lan_ns, 'link', 'set', 'lo', 'up'],
		['ip', '-n', d.lan_ns, 'link', 'set', d.lan_dev, 'up'],
	]) ?? save(d);

	if (err != null) {
		down(lan_vlan);
		return { error: `${err} [trunk ${trunk} from ${trunk_source}]` };
	}
	return info(d);
}

function wan_up(d, wan_vlan, wan_ip) {
	wan_vlan = vlan_id(wan_vlan);
	if (wan_vlan == null)
		return { error: 'wan_vlan must be 1..4094' };
	if (wan_vlan == d.lan_vlan)
		return { error: 'wan_vlan must differ from lan_vlan' };
	if (wan_ip == null) {
		if (wan_vlan > 255)
			return { error: `wan_vlan ${wan_vlan} has no 192.168.<vlan>.0/24 subnet, pass wan_ip` };
		wan_ip = `192.168.${wan_vlan}.${WAN_HOST}`;
	}
	if (type(wan_ip) != 'string' || length(iptoarr(wan_ip) ?? []) != 4)
		return { error: `bad wan_ip '${wan_ip}'` };

	let p = paths(d.lan_vlan);
	let subnet = join('.', slice(split(wan_ip, '.'), 0, 3));
	ns_destroy(d.wan_ns);
	for (let f in [p.server_pid, p.wan_dhcp_pid, p.wan_dhcp_leases])
		unlink(f);

	d.wan_vlan = wan_vlan;
	d.wan_ip = wan_ip;
	d.wan_mac = mac(1, wan_vlan);

	let err = run_all([
		['ip', 'netns', 'add', d.wan_ns],
		['ip', 'link', 'add', 'link', d.trunk, 'name', d.wan_dev, 'address', d.wan_mac,
			'type', 'vlan', 'id', wan_vlan],
		['ip', 'link', 'set', d.wan_dev, 'netns', d.wan_ns],
		['ip', '-n', d.wan_ns, 'link', 'set', 'lo', 'up'],
		['ip', 'netns', 'exec', d.wan_ns, 'sysctl', '-qw', `net.ipv6.conf.${d.wan_dev}.accept_ra=0`],
		['ip', '-n', d.wan_ns, 'addr', 'add', `${wan_ip}/24`, 'dev', d.wan_dev],
		['ip', '-n', d.wan_ns, 'link', 'set', d.wan_dev, 'up'],
		['ip', 'netns', 'exec', d.wan_ns, 'iperf3', '-s', '-D', '-B', wan_ip,
			'-I', p.server_pid, '--logfile', p.server_log],
		// The DUT's WAN lease (the lab host serves no DHCP); the lab host (.1)
		// stays the DUT's gateway, no DNS is handed out.
		['ip', 'netns', 'exec', d.wan_ns, 'dnsmasq',
			'--conf-file=/dev/null', '--port=0', '--user=root',
			`--interface=${d.wan_dev}`, '--bind-interfaces',
			`--dhcp-range=${subnet}.100,${subnet}.200,1h`,
			`--dhcp-option=option:router,${subnet}.1`,
			`--dhcp-leasefile=${p.wan_dhcp_leases}`,
			`--pid-file=${p.wan_dhcp_pid}`],
	]);

	if (err != null) {
		ns_destroy(d.wan_ns);
		d.wan_vlan = d.wan_ip = d.wan_mac = null;
		save(d);
		return { error: err };
	}
	err = save(d);
	return err ? { error: err } : info(d);
}

// DHCPv4 lease from the DUT: at most 5 tries of 3 s (inside rpcd's timeout).
function dhcp(d) {
	let e = need_lan(d);
	if (e) return e;

	let p = paths(d.lan_vlan);
	unlink(p.lease);
	let r = run(['ip', 'netns', 'exec', d.lan_ns,
		'udhcpc', '-f', '-n', '-q', '-t', '5', '-T', '3',
		'-i', d.lan_dev, '-x', `hostname:${d.hostname}`, '-s', DHCP_HOOK]);

	let data = readfile(p.lease);
	if (r.rc != 0 || data == null) {
		// Tell "the VLAN doesn't reach us" apart from "nobody serves DHCP"
		let st = link_stats(d);
		let hint = (st?.rx_packets == 0)
			? `nothing received on VLAN ${d.lan_vlan}: is it tagged on the probe's switch port (${d.trunk})?`
			: `${st?.rx_packets ?? '?'} packets received on VLAN ${d.lan_vlan} but no DHCP offer: is the DUT's DHCP server running?`;
		return { error: `no DHCP lease on ${d.lan_dev} (udhcpc rc ${r.rc}); ${hint}`, stats: st, log: r.out };
	}
	try {
		return { lease: json(data), log: r.out };
	}
	catch (ex) {
		return { error: `bad lease file ${p.lease}: ${ex.message}` };
	}
}

function addresses(d) {
	let e = need_lan(d);
	if (e) return e;

	let r = run(['ip', '-n', d.lan_ns, '-j', 'addr', 'show', 'dev', d.lan_dev]);
	if (r.rc != 0)
		return { error: trim(r.out) };
	let links;
	try {
		links = json(r.out);
	}
	catch (ex) {
		return { error: `ip -j: ${ex.message}` };
	}
	let ipv4 = [], ipv6 = [];
	for (let link in links)
		for (let a in link.addr_info ?? [])
			push(a.family == 'inet6' ? ipv6 : ipv4,
			     { address: a.local, prefixlen: a.prefixlen, scope: a.scope });
	return { ipv4, ipv6 };
}

function ping(d, host, count) {
	let e = need_lan(d);
	if (e) return e;

	let r = run(['ip', 'netns', 'exec', d.lan_ns, 'ping', '-c', is_uint(count) ? int(count) : 3, '-W', '2', host]);
	let tx = match(r.out, /([0-9]+) packets transmitted/);
	let rx = match(r.out, /([0-9]+) packets received/);
	return { host, rc: r.rc, transmitted: tx ? int(tx[1]) : 0, received: rx ? int(rx[1]) : 0, out: r.out };
}

// Resolve `name` against `server` from the LAN namespace (busybox nslookup).
function resolve(d, name, server) {
	let e = need_lan(d);
	if (e) return e;

	let r = run(['ip', 'netns', 'exec', d.lan_ns, 'nslookup', name, server]);
	let found = [], in_answer = false;
	for (let line in split(r.out, '\n')) {
		if (index(line, 'Name:') == 0) {
			in_answer = true;
			continue;
		}
		let m = in_answer ? match(line, /^Address( [0-9]+)?:[[:space:]]*([^[:space:]]+)/) : null;
		if (m)
			push(found, m[2]);
	}
	return { name, server, rc: r.rc, addresses: found, out: r.out };
}

// iperf3 LAN -> WAN (WAN -> LAN with reverse) in the background; poll with
// speedtest_result(). udp: UDP instead of TCP; bitrate: iperf3 -b ("100M";
// TCP default unlimited, UDP default 1M). Callers serialise via the probe's
// labgrid place lock.
function speedtest_start(d, duration, reverse, streams, udp, bitrate) {
	let e = need_lan(d);
	if (e) return e;
	if (d.wan_ip == null || !ns_exists(d.wan_ns))
		return { error: 'no WAN side, call wan_up first' };

	duration = is_uint(duration) ? int(duration) : 10;
	streams = is_uint(streams) ? int(streams) : 1;
	if (duration < 1 || duration > 300 || streams < 1 || streams > 16)
		return { error: 'duration must be 1..300 s and streams 1..16' };
	if (bitrate != null && (type(bitrate) != 'string' || !match(bitrate, /^[0-9]+(\.[0-9]+)?[KMG]?$/)))
		return { error: `bad bitrate '${bitrate}' (iperf3 -b, e.g. "100M")` };

	let p = paths(d.lan_vlan);
	if (access(p.speedtest_out) && !access(p.speedtest_rc))
		return { error: 'speedtest already running' };
	unlink(p.speedtest_rc);
	// Exists right away, so speedtest_result() reports "running" from now on
	if (writefile(p.speedtest_out, '') == null)
		return { error: `${p.speedtest_out}: ${fs_error()}` };

	let argv = ['ip', 'netns', 'exec', d.lan_ns, 'iperf3', '-c', d.wan_ip, '-t', duration, '-P', streams, '-J'];
	if (reverse)
		push(argv, '-R');
	if (udp)
		push(argv, '-u');
	if (bitrate != null)
		push(argv, '-b', bitrate);
	let cmd = join(' ', map(argv, shq));
	system(`( ${cmd} >${shq(p.speedtest_out)} 2>&1; echo $? >${shq(p.speedtest_rc)} ) </dev/null >/dev/null 2>&1 &`);
	return { started: true, duration, reverse: !!reverse, streams, udp: !!udp, bitrate,
		server: d.wan_ip };
}

function speedtest_result(d) {
	let p = paths(d.lan_vlan);
	let rc = readfile(p.speedtest_rc);
	if (rc == null)
		return access(p.speedtest_out) ? { running: true } : { error: 'no speedtest started' };

	let out = readfile(p.speedtest_out) ?? '';
	let res = { running: false, rc: int(trim(rc)) };
	let report;
	try {
		report = json(out);
	}
	catch (ex) {
		res.error = `iperf3 printed no JSON: ${substr(out, 0, 512)}`;
		return res;
	}
	if (report.error) {
		res.error = report.error;
		return res;
	}
	let end = report.end ?? {};
	let test = report.start?.test_start ?? {};
	res.reverse = test.reverse == 1;
	res.udp = test.protocol == 'UDP';
	res.cpu = end.cpu_utilization_percent;
	if (res.udp) {
		// iperf3 >= 3.11 has sum_sent/sum_received for UDP too, older only sum
		let sent = end.sum_sent ?? end.sum ?? {}, received = end.sum_received ?? end.sum ?? {};
		res.sent_bps = sent.bits_per_second;
		res.received_bps = received.bits_per_second;
		res.jitter_ms = received.jitter_ms ?? end.sum?.jitter_ms;
		res.lost_packets = received.lost_packets ?? end.sum?.lost_packets;
		res.packets = received.packets ?? end.sum?.packets;
		res.lost_percent = received.lost_percent ?? end.sum?.lost_percent;
	}
	else {
		res.sent_bps = end.sum_sent?.bits_per_second;
		res.received_bps = end.sum_received?.bits_per_second;
		res.retransmits = end.sum_sent?.retransmits;
	}
	return res;
}

// One export list: ucode 2026-01-16 (OpenWrt 25.12) can't parse a statement
// following `export function f() {}`.
export { load, info, up, down, wan_up, dhcp, addresses, ping, resolve, speedtest_start, speedtest_result };
