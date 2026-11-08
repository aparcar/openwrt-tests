// labgrid-probe: a test client behind a DUT and a server in front of it, for
// openwrt-tests (drivers/labgridprobedriver.py, tests/test_probe_lan.py).
//
// No per-DUT configuration: the caller names the VLANs. Each DUT gets a slot,
// keyed by its LAN VLAN, with two network namespaces on the trunk port:
//
//   up(lan_vlan)        pb-lan<N>: VLAN device pbl<N>, a LAN client of the
//                       DUT (DHCP, DNS, SLAAC, iperf3 client)
//   wan_up(wan_vlan)    pb-wan<N>: VLAN device pbw<N>, 192.168.<wan>.2 by
//                       default; iperf3 server, the DHCP server for the
//                       DUT's WAN port (.100-.200) and its internet uplink:
//                       the DUT's router is the probe, which NATs to its
//                       management network (veth pbv<N> -> pbx<N> in the
//                       root namespace, fw4 masquerades out of mgmt)
//
// On a lab host (local mode, see config()), the slot's devices are macvlans
// on the host's own DUT interfaces instead of VLANs on a trunk.
//
// Traffic between the namespaces can only take the path through the DUT.
// Slot state is in <RUN_DIR>/<lan_vlan>/slot.json. Every exported function
// returns an object; failures carry an `error` string.
//
// On top of that, per slot:
//   wan_dhcp(options, ntp)  WAN DHCP server options; NTP server (busybox
//                       ntpd -l) on the WAN address, handed out as option 42
//   ra_capture()        Router Advertisements seen on the LAN, parsed
//   mcast_*()           IPv4 multicast joiner/sender in either namespace
//   speedtest_start()   also UDP (bitrate) and LAN-side servers (the DUT)

'use strict';

import { popen, readfile, writefile, access, mkdir, unlink, error as fs_error } from 'fs';
import { cursor } from 'uci';

// Overrides for off-device tests only; rpcd runs with a clean environment.
const RUN_DIR = getenv('LABGRID_PROBE_RUN_DIR') ?? '/var/run/labgrid-probe';
const NETNS_DIR = getenv('LABGRID_PROBE_NETNS_DIR') ?? '/var/run/netns';
const DHCP_HOOK = getenv('LABGRID_PROBE_DHCP_HOOK') ?? '/usr/share/udhcpc/labgrid-probe.script';
const LIBEXEC = getenv('LABGRID_PROBE_LIBEXEC') ?? '/usr/libexec/labgrid-probe';

const WAN_HOST = 2;   // 192.168.<wan>.2: not the lab host (.1), outside its DHCP range
// DNS servers for the DUT's WAN lease, reached through the probe's NAT (the
// lab host serves no DNS)
const WAN_DNS = ['1.1.1.1', '9.9.9.9'];

const MCAST_DEFAULT_PORT = 5001;

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

// Start argv in the background (stdin from /dev/null, output appended to
// log). Returns its PID or null. `ip netns exec` execs its command, so the
// PID is the command's.
function spawn(argv, log) {
	let p = popen(`${join(' ', map(argv, shq))} </dev/null >>${shq(log)} 2>&1 & echo $!`);
	if (p == null)
		return null;
	let out = trim(p.read('all') ?? '');
	p.close();
	return is_uint(out) ? int(out) : null;
}

function pid_alive(pid) {
	return pid != null && access(`/proc/${pid}`) == true;
}

function read_pids(file) {
	return map(filter(split(trim(readfile(file) ?? ''), /[[:space:]]+/), is_uint), x => int(x));
}

// Kill the PIDs listed in file, wait up to 2 s for them to exit (so their
// ports are free again), remove the file.
function kill_pids(file) {
	let pids = read_pids(file);
	if (length(pids)) {
		system(['kill', ...map(pids, x => '' + x)]);
		for (let i = 0; i < 20 && length(filter(pids, pid_alive)); i++)
			sleep(100);
	}
	unlink(file);
}

function tail(file, n) {
	let lines = split(trim(readfile(file) ?? ''), '\n');
	return join('\n', slice(lines, -(n ?? 5)));
}

function read_json(file) {
	let data = readfile(file);
	if (data == null)
		return null;
	try {
		return json(data);
	}
	catch (ex) {
		return null;
	}
}

function is_ipv4(s) {
	return type(s) == 'string' && length(iptoarr(s) ?? []) == 4;
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

// How the probe is deployed (UCI labgrid_probe.main, written by the Ansible
// role labgrid_probe; no config = trunk):
//   trunk  a probe of its own: slots are VLAN devices on its trunk port
//   local  the probe runs on the lab host: slots are macvlans on the host's
//          own DUT interfaces (vlan<N>, or a port the DUT is cabled to),
//          only those listed as `parent`, so callers can't attach to others
function config() {
	let c = cursor();
	let parents = c.get('labgrid_probe', 'main', 'parent') ?? [];
	return {
		mode: c.get('labgrid_probe', 'main', 'mode') ?? 'trunk',
		parents: type(parents) == 'array' ? parents : [parents],
	};
}

// A slot's link into one of its namespaces: a VLAN on the trunk (trunk
// mode) or a macvlan on the DUT interface (local mode; "private": it only
// talks to the wire, not to the host or other macvlans on that interface).
function link_add_cmd(d, parent, vlan, name, mac_addr) {
	if (d.mode == 'local')
		return ['ip', 'link', 'add', 'link', parent, 'name', name, 'address', mac_addr,
			'type', 'macvlan', 'mode', 'private'];
	return ['ip', 'link', 'add', 'link', d.trunk, 'name', name, 'address', mac_addr,
		'type', 'vlan', 'id', vlan];
}

function check_parent(cfg, parent) {
	if (type(parent) == 'string' && parent in cfg.parents)
		return null;
	return `'${parent}' is not one of this host's DUT interfaces (${join(', ', cfg.parents)})`;
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
		ntp_pid: `${base}/ntpd.pid`,
		ntp_log: `${base}/ntpd.log`,
		mcast_recv_pid: `${base}/mcast-recv.pid`,
		mcast_recv_out: `${base}/mcast-recv.json`,
		mcast_send_pid: `${base}/mcast-send.pid`,
		mcast_send_out: `${base}/mcast-send.json`,
		mcast_log: `${base}/mcast.log`,
	};
}

// Background helpers that could survive a namespace teardown: kill them by
// PID file.
function stop_helpers(lan_vlan) {
	let p = paths(lan_vlan);
	for (let f in [p.ntp_pid, p.mcast_recv_pid, p.mcast_send_pid])
		kill_pids(f);
}

// The /30 between a slot's WAN namespace and the root namespace (the DUT's
// internet uplink): 100.64.0.0/10 (shared address space, RFC 6598), unique
// per LAN VLAN, so it never clashes with a DUT or lab network.
// [root side, WAN namespace side]
function uplink_addrs(lan_vlan) {
	let net = sprintf('100.%d.%d', 64 + (lan_vlan >> 8), lan_vlan & 255);
	return [`${net}.1`, `${net}.2`];
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
	stop_helpers(lan_vlan);
	ns_destroy(`pb-lan${lan_vlan}`);
	ns_destroy(`pb-wan${lan_vlan}`);
	let p = paths(lan_vlan);
	for (let f in [p.slot, p.lease, p.server_pid, p.wan_dhcp_pid, p.wan_dhcp_leases,
	               p.speedtest_out, p.speedtest_rc, p.mcast_recv_out, p.mcast_send_out,
	               p.ntp_log, p.mcast_log])
		unlink(f);
	return { ok: true };
}

// trunk mode: lan_vlan is the DUT's LAN VLAN, trunk the port carrying it.
// local mode: parent is the host's interface towards the DUT's LAN;
// lan_vlan its VLAN if it has one, else the slot gets 4000 + the parent's
// ifindex (the reply's lan_vlan is the slot id the other calls take).
function up(lan_vlan, trunk, parent) {
	let cfg = config();
	let trunk_source = 'argument';
	if (cfg.mode == 'local') {
		let e = check_parent(cfg, parent);
		if (e) return { error: e };
		if (lan_vlan == null) {
			let idx = int(trim(readfile(`/sys/class/net/${parent}/ifindex`) ?? '0'));
			lan_vlan = (idx >= 1 && idx <= 94) ? 4000 + idx : null;
			if (lan_vlan == null)
				return { error: `${parent}: no slot id (ifindex ${idx} > 94), pass lan_vlan` };
		}
		else
			lan_vlan = vlan_id(lan_vlan);
		trunk = parent;
		trunk_source = 'local';
	}
	else if (lan_vlan == null)
		return { error: 'this probe puts slots on DUT VLANs: lan_vlan needed', needs_vlan: true };
	else {
		lan_vlan = vlan_id(lan_vlan);
		if (trunk == null) {
			let found = default_trunk();   // ucode has no destructuring
			trunk = found[0];
			trunk_source = found[1];
		}
	}
	if (lan_vlan == null)
		return { error: 'lan_vlan must be 1..4094' };
	if (type(trunk) != 'string' || !match(trunk, /^[A-Za-z0-9_.-]{1,15}$/))
		return { error: `bad trunk device name '${trunk}'` };

	down(lan_vlan);

	let d = {
		mode: cfg.mode,
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
		// local mode: the host's interface, which it brings up itself
		...(d.mode == 'local' ? [] : [['ip', 'link', 'set', trunk, 'up']]),
		['ip', 'netns', 'add', d.lan_ns],
		link_add_cmd(d, trunk, lan_vlan, d.lan_dev, d.lan_mac),
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

// The DUT's WAN DHCP server (the lab host serves no DHCP), restarted with
// the slot's current options. The probe's WAN address is the DUT's router
// (NAT to the internet), WAN_DNS its DNS servers. Extra options are forced
// (sent even if the client didn't ask), so e.g. option 28 or 42 reach udhcpc.
// The lease file is kept, so the DUT keeps its address across restarts.
// Returns an error or null.
function wan_dhcp_start(d) {
	let p = paths(d.lan_vlan);
	kill_pids(p.wan_dhcp_pid);
	let subnet = join('.', slice(split(d.wan_ip, '.'), 0, 3));
	let argv = ['ip', 'netns', 'exec', d.wan_ns, 'dnsmasq',
		'--conf-file=/dev/null', '--port=0', '--user=root',
		`--interface=${d.wan_dev}`, '--bind-interfaces',
		`--dhcp-range=${subnet}.100,${subnet}.200,1h`,
		`--dhcp-option=option:router,${d.wan_ip}`,
		`--dhcp-option=option:dns-server,${join(',', WAN_DNS)}`,
		`--dhcp-leasefile=${p.wan_dhcp_leases}`,
		`--pid-file=${p.wan_dhcp_pid}`];
	if (d.wan_ntp)
		push(argv, `--dhcp-option-force=option:ntp-server,${d.wan_ip}`);
	for (let o in d.wan_dhcp_options ?? [])
		push(argv, `--dhcp-option-force=${o}`);
	let r = run(argv);
	return r.rc == 0 ? null : `dnsmasq: ${trim(r.out)} (rc ${r.rc})`;
}

// local mode: wan_parent is the host's interface towards the DUT's WAN
// (default vlan<wan_vlan>). Without wan_vlan (a port the DUT's WAN is
// cabled to straight), the WAN id is 4000 + the port's ifindex and the
// subnet 172.16.<ifindex>.0/24 (in the WAN namespace only).
function wan_up(d, wan_vlan, wan_ip, wan_parent) {
	if (d.mode == 'local' && wan_vlan == null && wan_parent != null) {
		let e = check_parent(config(), wan_parent);
		if (e) return { error: e };
		let idx = int(trim(readfile(`/sys/class/net/${wan_parent}/ifindex`) ?? '0'));
		if (idx < 1 || idx > 94)
			return { error: `${wan_parent}: no WAN id (ifindex ${idx}), pass wan_vlan` };
		wan_vlan = 4000 + idx;
		if (wan_ip == null)
			wan_ip = `172.16.${idx}.${WAN_HOST}`;
	}
	wan_vlan = vlan_id(wan_vlan);
	if (wan_vlan == null)
		return { error: 'wan_vlan must be 1..4094' };
	if (wan_vlan == d.lan_vlan)
		return { error: 'wan_vlan must differ from lan_vlan' };
	if (d.mode == 'local') {
		if (wan_parent == null)
			wan_parent = `vlan${wan_vlan}`;
		let e = check_parent(config(), wan_parent);
		if (e) return { error: e };
		if (wan_parent == d.trunk)
			return { error: `${wan_parent} is the slot's LAN side` };
	}
	if (wan_ip == null) {
		if (wan_vlan > 255)
			return { error: `wan_vlan ${wan_vlan} has no 192.168.<vlan>.0/24 subnet, pass wan_ip` };
		wan_ip = `192.168.${wan_vlan}.${WAN_HOST}`;
	}
	if (!is_ipv4(wan_ip))
		return { error: `bad wan_ip '${wan_ip}'` };

	let p = paths(d.lan_vlan);
	// Everything on the WAN side starts over, with default DHCP options
	kill_pids(p.ntp_pid);
	ns_destroy(d.wan_ns);
	for (let f in [p.server_pid, p.wan_dhcp_pid, p.wan_dhcp_leases])
		unlink(f);

	d.wan_vlan = wan_vlan;
	d.wan_ip = wan_ip;
	d.wan_mac = mac(1, wan_vlan);
	d.wan_dhcp_options = [];
	d.wan_ntp = false;

	// The DUT's internet uplink: a veth from the WAN namespace (pbv<N>,
	// masqueraded) to the root namespace (pbx<N>), from where fw4 forwards
	// and masquerades out of mgmt (files/etc/config/firewall, zone dut_wan).
	// Deleting the namespace deletes the pair.
	let uplink = uplink_addrs(d.lan_vlan);
	let root_dev = `pbx${d.lan_vlan}`, ns_dev = `pbv${d.lan_vlan}`;
	run(['ip', 'link', 'del', root_dev]);   // left over from a crashed run

	let err = run_all([
		['ip', 'netns', 'add', d.wan_ns],
		link_add_cmd(d, wan_parent, wan_vlan, d.wan_dev, d.wan_mac),
		['ip', 'link', 'set', d.wan_dev, 'netns', d.wan_ns],
		['ip', '-n', d.wan_ns, 'link', 'set', 'lo', 'up'],
		['ip', 'netns', 'exec', d.wan_ns, 'sysctl', '-qw', `net.ipv6.conf.${d.wan_dev}.accept_ra=0`],
		['ip', '-n', d.wan_ns, 'addr', 'add', `${wan_ip}/24`, 'dev', d.wan_dev],
		['ip', '-n', d.wan_ns, 'link', 'set', d.wan_dev, 'up'],
		['ip', 'link', 'add', root_dev, 'type', 'veth', 'peer', 'name', ns_dev, 'netns', d.wan_ns],
		['ip', 'addr', 'add', `${uplink[0]}/30`, 'dev', root_dev],
		['ip', 'link', 'set', root_dev, 'up'],
		['ip', '-n', d.wan_ns, 'addr', 'add', `${uplink[1]}/30`, 'dev', ns_dev],
		['ip', '-n', d.wan_ns, 'link', 'set', ns_dev, 'up'],
		['ip', '-n', d.wan_ns, 'route', 'add', 'default', 'via', uplink[0]],
		['ip', 'netns', 'exec', d.wan_ns, 'sysctl', '-qw', 'net.ipv4.ip_forward=1'],
		['ip', 'netns', 'exec', d.wan_ns, 'nft',
			`add table ip nat; add chain ip nat post { type nat hook postrouting priority srcnat; }; add rule ip nat post oifname "${ns_dev}" masquerade`],
		['ip', 'netns', 'exec', d.wan_ns, 'iperf3', '-s', '-D', '-B', wan_ip,
			'-I', p.server_pid, '--logfile', p.server_log],
	]) ?? wan_dhcp_start(d);

	if (err != null) {
		ns_destroy(d.wan_ns);
		d.wan_vlan = d.wan_ip = d.wan_mac = null;
		save(d);
		return { error: err };
	}
	err = save(d);
	return err ? { error: err } : info(d);
}

function need_wan(d) {
	return (d.wan_ip != null && ns_exists(d.wan_ns)) ? null : { error: 'no WAN side, call wan_up first' };
}

// NTP server on the WAN address: busybox ntpd -l without peers serves the
// probe's clock (synced over its management uplink) as stratum 1.
function wan_ntp_start(d) {
	let p = paths(d.lan_vlan);
	kill_pids(p.ntp_pid);
	unlink(p.ntp_log);
	let pid = spawn(['ip', 'netns', 'exec', d.wan_ns, 'ntpd', '-n', '-l', '-I', d.wan_dev], p.ntp_log);
	if (pid == null)
		return 'ntpd did not start';
	writefile(p.ntp_pid, `${pid}\n`);
	sleep(300);
	if (!pid_alive(pid)) {
		unlink(p.ntp_pid);
		return `ntpd exited: ${tail(p.ntp_log)}`;
	}
	return null;
}

// Replace the WAN DHCP server's extra options ("<code>,<value>" or
// "option:<name>,<value>", as dnsmasq's --dhcp-option) and switch the NTP
// server on/off. The DUT sees the change at its next DHCP request.
function wan_dhcp(d, options, ntp) {
	let e = need_wan(d);
	if (e) return e;

	options = options ?? [];
	if (type(options) != 'array' || length(options) > 32)
		return { error: 'options must be a list of at most 32 strings' };
	for (let o in options)
		if (type(o) != 'string' || length(o) > 255 ||
		    !match(o, /^(option:[a-z0-9-]+|[0-9]{1,3}),[^[:cntrl:]]*$/))
			return { error: `bad DHCP option '${o}': use "<code>,<value>" or "option:<name>,<value>"` };

	let p = paths(d.lan_vlan);
	let err = null;
	if (ntp)
		err = wan_ntp_start(d);
	else
		kill_pids(p.ntp_pid);
	if (err != null)
		return { error: err };

	d.wan_dhcp_options = options;
	d.wan_ntp = !!ntp;
	err = wan_dhcp_start(d) ?? save(d);
	if (err != null)
		return { error: err };
	let res = info(d);
	res.ntp_server = d.wan_ntp ? d.wan_ip : null;
	return res;
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

// The answer addresses of busybox nslookup output.
function nslookup_addresses(out) {
	let found = [], in_answer = false;
	for (let line in split(out, '\n')) {
		if (index(line, 'Name:') == 0) {
			in_answer = true;
			continue;
		}
		let m = in_answer ? match(line, /^Address( [0-9]+)?:[[:space:]]*([^[:space:]]+)/) : null;
		if (m)
			push(found, m[2]);
	}
	return found;
}

// Resolve `name` against `server` from the LAN namespace (busybox nslookup).
function resolve(d, name, server) {
	let e = need_lan(d);
	if (e) return e;

	let r = run(['ip', 'netns', 'exec', d.lan_ns, 'nslookup', name, server]);
	return { name, server, rc: r.rc, addresses: nslookup_addresses(r.out), out: r.out };
}

// iperf3 from the LAN namespace in the background; poll with
// speedtest_result(). The server is the probe's WAN side by default (traffic
// through the DUT's NAT), or `server`: an IPv4 address reachable from the
// LAN, e.g. the DUT itself running `iperf3 -s` (boards without a WAN port).
// opts: duration (s, 1..3600), reverse (server -> LAN), streams (1..16),
// udp, bitrate (iperf3 -b: "100M"; TCP default unlimited, UDP default 1M),
// server, port. Callers serialise via the probe's labgrid place lock.
function speedtest_start(d, opts) {
	let e = need_lan(d);
	if (e) return e;
	opts = opts ?? {};

	let server = opts.server;
	if (server == null) {
		e = need_wan(d);
		if (e) return { error: `${e.error} (or pass server)` };
		server = d.wan_ip;
	}
	if (!is_ipv4(server))
		return { error: `bad server '${server}'` };

	let duration = is_uint(opts.duration) ? int(opts.duration) : 10;
	let streams = is_uint(opts.streams) ? int(opts.streams) : 1;
	let port = is_uint(opts.port) ? int(opts.port) : 5201;
	if (duration < 1 || duration > 3600 || streams < 1 || streams > 16 || port < 1 || port > 65535)
		return { error: 'duration must be 1..3600 s, streams 1..16, port 1..65535' };
	let bitrate = opts.bitrate;
	if (bitrate != null && (type(bitrate) != 'string' || !match(bitrate, /^[0-9]+(\.[0-9]+)?[KMG]?$/)))
		return { error: `bad bitrate '${bitrate}' (iperf3 -b, e.g. "100M")` };

	let p = paths(d.lan_vlan);
	if (access(p.speedtest_out) && !access(p.speedtest_rc))
		return { error: 'speedtest already running' };
	unlink(p.speedtest_rc);
	// Exists right away, so speedtest_result() reports "running" from now on
	if (writefile(p.speedtest_out, '') == null)
		return { error: `${p.speedtest_out}: ${fs_error()}` };

	let argv = ['ip', 'netns', 'exec', d.lan_ns, 'iperf3', '-c', server, '-p', port,
		'-t', duration, '-P', streams, '-J'];
	if (opts.reverse)
		push(argv, '-R');
	if (opts.udp)
		push(argv, '-u');
	if (bitrate != null)
		push(argv, '-b', bitrate);
	let cmd = join(' ', map(argv, shq));
	system(`( ${cmd} >${shq(p.speedtest_out)} 2>&1; echo $? >${shq(p.speedtest_rc)} ) </dev/null >/dev/null 2>&1 &`);
	return { started: true, duration, reverse: !!opts.reverse, streams, udp: !!opts.udp,
		bitrate, server, port };
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

// --- IPv6 router advertisements -------------------------------------------------

// RAs on the LAN VLAN, parsed (labgrid_probe_ra.uc). Sends a Router
// Solicitation first unless solicit is false. Waits up to timeout s
// (1..20) for count RAs (default 1; 0 = all within the timeout).
function ra_capture(d, timeout, solicit, count) {
	let e = need_lan(d);
	if (e) return e;

	timeout = is_uint(timeout) ? int(timeout) : 5;
	count = is_uint(count) ? int(count) : 1;
	if (timeout < 1 || timeout > 20 || count > 100)
		return { error: 'timeout must be 1..20 s, count 0..100' };

	let r = run(['ip', 'netns', 'exec', d.lan_ns, `${LIBEXEC}/ra-capture`, d.lan_dev,
		timeout * 1000, solicit === false ? 0 : 1, count]);
	let res;
	try {
		res = json(r.out);
	}
	catch (ex) {
		return { error: `ra-capture (rc ${r.rc}): ${substr(r.out, 0, 512)}` };
	}
	return res;
}

// --- multicast --------------------------------------------------------------------

// [namespace, device] for side "lan"/"wan", or an error object
function side_ns(d, side) {
	if (side == 'wan') {
		let e = need_wan(d);
		return e ?? [d.wan_ns, d.wan_dev];
	}
	if (side == 'lan')
		return need_lan(d) ?? [d.lan_ns, d.lan_dev];
	return { error: `side must be "lan" or "wan", not '${side}'` };
}

function mcast_group_ok(g) {
	let a = is_ipv4(g) ? iptoarr(g) : null;
	return a != null && a[0] >= 224 && a[0] <= 239;
}

function mcast_status(d) {
	let p = paths(d.lan_vlan);
	let recv = read_json(p.mcast_recv_out), send = read_json(p.mcast_send_out);
	if (recv != null)
		recv.running = pid_alive(read_pids(p.mcast_recv_pid)[0]);
	if (send != null)
		send.running = pid_alive(read_pids(p.mcast_send_pid)[0]);
	return { receiver: recv, sender: send };
}

// Join group on one side (default LAN) and count what arrives. igmp_version
// 2 or 3 forces the IGMP version of the joining interface (0: kernel default).
function mcast_join(d, group, port, side, igmp_version) {
	if (!mcast_group_ok(group))
		return { error: `bad multicast group '${group}' (224.0.0.0/4)` };
	port = is_uint(port) ? int(port) : MCAST_DEFAULT_PORT;
	igmp_version = is_uint(igmp_version) ? int(igmp_version) : 0;
	if (port < 1 || port > 65535 || !(igmp_version in [0, 2, 3]))
		return { error: 'port must be 1..65535, igmp_version 0, 2 or 3' };
	let where = side_ns(d, side ?? 'lan');
	if (type(where) == 'object') return where;

	let p = paths(d.lan_vlan);
	kill_pids(p.mcast_recv_pid);
	unlink(p.mcast_recv_out);
	let r = run(['ip', 'netns', 'exec', where[0], 'sysctl', '-qw',
		`net.ipv4.conf.${where[1]}.force_igmp_version=${igmp_version}`]);
	if (r.rc != 0)
		return { error: `force_igmp_version: ${trim(r.out)}` };

	let pid = spawn(['ip', 'netns', 'exec', where[0], `${LIBEXEC}/mcast`, 'recv', group, port,
		where[1], p.mcast_recv_out], p.mcast_log);
	if (pid == null)
		return { error: 'could not start the multicast receiver' };
	writefile(p.mcast_recv_pid, `${pid}\n`);
	for (let i = 0; i < 20 && !access(p.mcast_recv_out) && pid_alive(pid); i++)
		sleep(100);
	let st = read_json(p.mcast_recv_out);
	if (st?.joined != true) {
		kill_pids(p.mcast_recv_pid);
		return { error: `multicast join failed: ${st?.error ?? tail(p.mcast_log)}` };
	}
	return mcast_status(d);
}

// Send count datagrams to group:port from one side (default WAN), one every
// interval ms, with the given TTL (default 8: it has to cross the DUT). In
// the background; poll mcast_result().
function mcast_send(d, group, port, side, count, interval, ttl) {
	if (!mcast_group_ok(group))
		return { error: `bad multicast group '${group}' (224.0.0.0/4)` };
	port = is_uint(port) ? int(port) : MCAST_DEFAULT_PORT;
	count = is_uint(count) ? int(count) : 10;
	interval = is_uint(interval) ? int(interval) : 100;
	ttl = is_uint(ttl) ? int(ttl) : 8;
	if (port < 1 || port > 65535 || count < 1 || count > 100000 || interval < 1 ||
	    interval > 10000 || ttl < 1 || ttl > 255 || count * interval > 3600000)
		return { error: 'port 1..65535, count 1..100000, interval 1..10000 ms (at most 1 h in total), ttl 1..255' };
	let where = side_ns(d, side ?? 'wan');
	if (type(where) == 'object') return where;

	let p = paths(d.lan_vlan);
	if (pid_alive(read_pids(p.mcast_send_pid)[0]))
		return { error: 'multicast sender already running' };
	unlink(p.mcast_send_out);
	let pid = spawn(['ip', 'netns', 'exec', where[0], `${LIBEXEC}/mcast`, 'send', group, port,
		where[1], p.mcast_send_out, count, interval, ttl], p.mcast_log);
	if (pid == null)
		return { error: 'could not start the multicast sender' };
	writefile(p.mcast_send_pid, `${pid}\n`);
	return { started: true, group, port, side: side ?? 'wan', count, interval, ttl };
}

function mcast_result(d) {
	return mcast_status(d);
}

// Stop receiver (the kernel sends the IGMP leave) and sender; returns the
// final counts.
function mcast_leave(d) {
	let p = paths(d.lan_vlan);
	kill_pids(p.mcast_recv_pid);
	kill_pids(p.mcast_send_pid);
	return mcast_status(d);
}

// One export list: ucode 2026-01-16 (OpenWrt 25.12) can't parse a statement
// following `export function f() {}`.
export {
	load, info, up, down, wan_up, wan_dhcp, dhcp, addresses, ping, resolve,
	speedtest_start, speedtest_result, ra_capture,
	mcast_join, mcast_send, mcast_result, mcast_leave
};
