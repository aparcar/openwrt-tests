// labgrid-wifi-probe: many Wi-Fi clients at once against a DUT's AP, for
// openwrt-tests (drivers/labgridprobedriver.py LabgridWifiProbeDriver,
// tests/test_wifi_clients.py).
//
// Made for ath9k radios (TL-WDR4300): ath9k allows up to 2048 station
// interfaces on one channel. One set of stations per radio, keyed by phy:
//
//   sta_up(args)        creates ws<n>s<i> (n: phy number, i: station) on the
//                       radio for args.band and starts the helper
//                       (libexec/labgrid-probe/wifi-sta) in the background:
//                       wpa_supplicant in batches, then a DHCP lease from
//                       the DUT for every station that is connected
//   sta_status(phy)     per station: connected (operstate up: associated and
//                       keys installed), carrier_changes, lease; with
//                       detail also bssid, freq, signal from `iw dev link`
//   sta_ping(phy, ...)  ping from every (or the listed) leased stations
//   sta_down(band)      stops everything, removes interfaces and routes
//
// MACs: the radio's address, locally administered, byte 4 = n * 16 + i / 256,
// byte 5 = i % 256. Only the last two bytes differ between stations, which
// keeps ath9k's BSSID mask (the addresses the hardware ACKs) narrow.
//
// Routing: cfg80211 interfaces can't move into another network namespace on
// their own, so every station lives in the root namespace. Each lease is a
// /32 on its station with a routing table of its own:
//
//   rule  pref <T> from <ip> iif lo lookup <T>     (locally generated only)
//   table <T>: <subnet> dev ws<n>s<i> src <ip>, default via <router>
//
// The local table's rule moves behind those rules (pref 0 -> LOCAL_PREF), so
// a ping from one station to another station's address leaves over the air
// (and through the AP) instead of looping back. Incoming packets don't match
// the `iif lo` rules and reach the local table as usual. Per station:
// accept_local=1 (packets from another station's address are not
// martians), and arp_ignore=1/arp_announce=2 (only the station holding an
// address answers ARP for it).
//
// State: <RUN_DIR>/<phy>/request.json (written by sta_up), state.json (kept
// up to date by the helper), *.pid, lease-<ifname>.json (udhcpc hook).
// Every exported function returns an object; failures carry `error`.

'use strict';

import { popen, readfile, writefile, access, mkdir, unlink, lsdir, error as fs_error } from 'fs';

const RUN_DIR = getenv('LABGRID_WIFI_PROBE_RUN_DIR') ?? '/var/run/labgrid-wifi-probe';
// sta_down moves a set's directory here (one per phy, the last run)
const LAST_DIR = `${RUN_DIR}.last`;
const LIBEXEC = getenv('LABGRID_PROBE_LIBEXEC') ?? '/usr/libexec/labgrid-probe';
const DHCP_HOOK = getenv('LABGRID_WIFI_PROBE_DHCP_HOOK') ?? '/usr/share/udhcpc/labgrid-wifi-probe.script';

const MAX_STATIONS = 256;
// Routing table and rule preference of station i on phy n
const TABLE_BASE = 1000;
const PHY_STRIDE = 1024;
// Where the local table's rule goes: behind every station rule
const LOCAL_PREF = 30000;
const DHCP_PARALLEL = 16;
const DEFAULT_BATCH = 8;
const DEFAULT_STAGGER_MS = 1000;
const MAX_PING_COUNT = 20;   // sta_ping must finish within rpcd's 30 s

const BANDS = { '2g': [2400, 2500], '5g': [5150, 5895] };

// wpa_supplicant network lines per encryption (OpenWrt's names)
const ENCRYPTIONS = {
	'none': ['key_mgmt=NONE'],
	'psk': ['key_mgmt=WPA-PSK', 'proto=WPA', 'pairwise=CCMP TKIP', 'group=CCMP TKIP'],
	'psk2': ['key_mgmt=WPA-PSK', 'proto=RSN', 'pairwise=CCMP'],
	'psk-mixed': ['key_mgmt=WPA-PSK', 'proto=RSN WPA', 'pairwise=CCMP TKIP', 'group=CCMP TKIP'],
	'sae': ['key_mgmt=SAE', 'proto=RSN', 'pairwise=CCMP', 'ieee80211w=2'],
	'sae-mixed': ['key_mgmt=SAE WPA-PSK', 'proto=RSN', 'pairwise=CCMP', 'ieee80211w=1'],
};

// --- helpers (declared before use: ucode doesn't hoist) ---------------------

function shq(s) {
	return `'${replace('' + s, "'", "'\\''")}'`;
}

function is_uint(v) {
	return v != null && match('' + v, /^[0-9]+$/) != null;
}

function is_ipv4(s) {
	return type(s) == 'string' && length(iptoarr(s) ?? []) == 4;
}

// Run argv through /bin/sh with every argument quoted. Returns { rc, out }.
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

// Start argv in the background, output appended to log. Returns its PID.
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

// Kill the PIDs in file, wait up to 2 s for them to exit, remove the file.
function kill_pidfile(file) {
	let pids = read_pids(file);
	if (length(pids)) {
		system(['kill', ...map(pids, x => '' + x)]);
		for (let i = 0; i < 20 && length(filter(pids, pid_alive)); i++)
			sleep(100);
	}
	unlink(file);
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

function tail(file, n) {
	let lines = split(trim(readfile(file) ?? ''), '\n');
	return join('\n', slice(lines, -n));
}

function save_json(file, data) {
	writefile(file, sprintf('%J\n', data));
}

function sysfs(path) {
	let v = readfile(path);
	return v == null ? null : trim(v);
}

function words(s) {
	return filter(split(trim(s ?? ''), /[[:space:]]+/), x => length(x));
}

// Seconds since t0 (clock(true): monotonic [s, ns]), one decimal
function since(t0) {
	let c = clock(true);
	let ms = (c[0] - t0[0]) * 1000 + (c[1] - t0[1]) / 1000000;
	return ms / 100 / 10.0;
}

function mem_available_kb() {
	let m = match(readfile('/proc/meminfo') ?? '', /MemAvailable:[[:space:]]+([0-9]+)/);
	return m ? int(m[1]) : null;
}

// --- radios and stations ------------------------------------------------------

function phy_index(phy) {
	let m = match(phy ?? '', /^phy([0-9]+)$/);
	return m ? int(m[1]) : null;
}

function phys() {
	return sort(filter(lsdir('/sys/class/ieee80211') ?? [], p => phy_index(p) != null));
}

// Enabled frequencies (MHz) of a phy
function phy_freqs(phy) {
	let r = run(['iw', 'phy', phy, 'info']);
	let freqs = [];
	if (r.rc != 0)
		return freqs;
	for (let line in split(r.out, '\n')) {
		let m = match(line, /^[[:space:]]*\* ([0-9]+)(\.[0-9]+)? MHz/);
		if (m && index(line, 'disabled') < 0)
			push(freqs, int(m[1]));
	}
	return freqs;
}

function phy_bands(freqs) {
	let bands = [];
	for (let band, range in BANDS)
		if (length(filter(freqs, f => f >= range[0] && f <= range[1])))
			push(bands, band);
	return bands;
}

function station_name(n, i) {
	return `ws${n}s${i}`;
}

// [n, i] of a station interface name, or null
function station_ids(ifname) {
	let m = match(ifname ?? '', /^ws([0-9]+)s([0-9]+)$/);
	return m ? [int(m[1]), int(m[2])] : null;
}

function table_id(n, i) {
	return TABLE_BASE + n * PHY_STRIDE + i;
}

function station_mac(phy, i) {
	let b = split(sysfs(`/sys/class/ieee80211/${phy}/macaddress`) ?? '', ':');
	if (length(b) != 6)
		return null;
	let n = phy_index(phy);
	return sprintf('%02x:%s:%s:%s:%02x:%02x', hex(b[0]) | 2, b[1], b[2], b[3], n * 16 + i / 256, i % 256);
}

// Network interfaces on a phy, ours (stations) and others (netifd's, a scan0)
function phy_netdevs(phy) {
	let ours = [], others = [];
	for (let dev in lsdir('/sys/class/net') ?? []) {
		if (sysfs(`/sys/class/net/${dev}/phy80211/name`) != phy)
			continue;
		push(station_ids(dev) ? ours : others, dev);
	}
	return { ours, others };
}

function phy_dir(phy) {
	return `${RUN_DIR}/${phy}`;
}

function load(phy) {
	return read_json(`${phy_dir(phy)}/request.json`);
}

// The phy whose stations were set up for band, or null
function phy_up(band) {
	for (let phy in lsdir(RUN_DIR) ?? [])
		if (load(phy)?.band == band)
			return phy;
	return null;
}

function phy_for_band(band) {
	for (let phy in phys())
		if (index(phy_bands(phy_freqs(phy)), band) >= 0)
			return phy;
	return null;
}

function radios() {
	let out = [];
	for (let phy in phys()) {
		let devs = phy_netdevs(phy);
		let req = load(phy);
		push(out, {
			phy,
			bands: phy_bands(phy_freqs(phy)),
			mac: sysfs(`/sys/class/ieee80211/${phy}/macaddress`),
			busy: devs.others,
			stations: length(devs.ours),
			up: req ? req.band : null,
		});
	}
	return { radios: out, mem_available_kb: mem_available_kb() };
}

function wpa_conf(req) {
	let lines = [
		'network={',
		`	ssid=${hexenc(req.ssid)}`,
	];
	if (req.hidden)
		push(lines, '	scan_ssid=1');
	if (req.bssid)
		push(lines, `	bssid=${req.bssid}`);
	if (req.freq)
		push(lines, `	scan_freq=${req.freq}`, `	freq_list=${req.freq}`);
	for (let l in ENCRYPTIONS[req.encryption])
		push(lines, `	${l}`);
	if (req.encryption != 'none')
		push(lines, `	psk="${req.key}"`);
	push(lines, '}', '');
	return join('\n', lines);
}

// --- routing (shared by the helper and the udhcpc hook) -------------------------

function routing_setup() {
	// log_martians: the kernel logs packets it drops for their source
	// address, which sta_ping's diag reports
	for (let key, v in { 'all/arp_ignore': 1, 'all/arp_announce': 2, 'all/rp_filter': 0,
	                     'all/log_martians': 1 })
		writefile(`/proc/sys/net/ipv4/conf/${key}`, `${v}\n`);
	let r = run(['ip', 'rule', 'show', 'pref', '' + LOCAL_PREF]);
	if (index(r.out, 'lookup local') < 0) {
		let err = run_all([['ip', 'rule', 'add', 'pref', '' + LOCAL_PREF, 'lookup', 'local']]);
		if (err)
			return err;
	}
	// Already moved by another radio's set: fine
	run(['ip', 'rule', 'del', 'pref', '0', 'lookup', 'local']);
	return null;
}

function routing_restore() {
	let r = run(['ip', 'rule', 'show', 'pref', '0']);
	if (index(r.out, 'lookup local') < 0)
		run(['ip', 'rule', 'add', 'pref', '0', 'lookup', 'local']);
	run(['ip', 'rule', 'del', 'pref', '' + LOCAL_PREF, 'lookup', 'local']);
}

function network_of(ip, prefix) {
	let a = iptoarr(ip), out = [];
	for (let k = 0; k < 4; k++) {
		let bits = prefix - k * 8;
		let mask = bits >= 8 ? 255 : (bits <= 0 ? 0 : (0xff << (8 - bits)) & 0xff);
		push(out, a[k] & mask);
	}
	return arrtoip(out);
}

function station_routes_del(n, i) {
	let t = '' + table_id(n, i);
	run(['ip', 'rule', 'del', 'pref', t]);
	run(['ip', 'route', 'flush', 'table', t]);
}

// udhcpc hook (labgrid-wifi-probe.script): env holds udhcpc's variables.
// Returns the exit code.
function dhcp_hook(action, env) {
	let ifn = env.interface;
	let ids = station_ids(ifn);
	if (ids == null)
		return 1;
	let n = ids[0], i = ids[1];
	let lease_file = `${RUN_DIR}/phy${n}/lease-${ifn}.json`;
	let t = '' + table_id(n, i);

	if (action == 'deconfig') {
		run(['ip', '-4', 'addr', 'flush', 'dev', ifn]);
		station_routes_del(n, i);
		unlink(lease_file);
		return 0;
	}
	if (action != 'bound' && action != 'renew')
		return 0;

	let ip = env.ip;
	let prefix = is_uint(env.mask) ? int(env.mask) : 24;
	if (!is_ipv4(ip))
		return 1;
	let routers = words(env.router);
	let cmds = [
		['ip', '-4', 'addr', 'flush', 'dev', ifn],
		['ip', 'addr', 'add', `${ip}/32`, 'dev', ifn],
		['ip', 'route', 'replace', `${network_of(ip, prefix)}/${prefix}`, 'dev', ifn, 'src', ip, 'table', t],
	];
	if (length(routers))
		push(cmds, ['ip', 'route', 'replace', 'default', 'via', routers[0], 'dev', ifn, 'src', ip, 'table', t]);
	let err = run_all(cmds);
	run(['ip', 'rule', 'del', 'pref', t]);
	err = err ?? run_all([['ip', 'rule', 'add', 'pref', t, 'from', ip, 'iif', 'lo', 'lookup', t]]);
	if (err) {
		warn(`${err}\n`);
		return 1;
	}
	save_json(lease_file, {
		interface: ifn,
		ip,
		mask: prefix,
		router: routers,
		dns: words(env.dns),
		serverid: env.serverid,
		lease: is_uint(env.lease) ? int(env.lease) : 0,
	});
	return 0;
}

// --- the helper (libexec/labgrid-probe/wifi-sta <dir>) ----------------------------

function station_sysctls(ifn) {
	for (let kv in [
		['ipv4', 'rp_filter', 0],
		['ipv4', 'accept_local', 1],
		['ipv4', 'arp_ignore', 1],
		['ipv4', 'arp_announce', 2],
		['ipv6', 'disable_ipv6', 1],
	])
		writefile(`/proc/sys/net/${kv[0]}/conf/${ifn}/${kv[1]}`, `${kv[2]}\n`);
}

// Creates the stations, starts wpa_supplicant in batches, then keeps every
// connected station leased until sta_down kills it. Returns the exit code.
function helper_run(dir) {
	let req = read_json(`${dir}/request.json`);
	if (req == null)
		return 1;
	let n = phy_index(req.phy);
	let count = length(req.stations);
	let t0 = clock(true);
	let st = { phase: 'creating', connected: 0, leases: 0 };
	let save = () => save_json(`${dir}/state.json`, st);
	let fail = msg => {
		st.phase = 'failed';
		st.error = msg;
		save();
		return 1;
	};
	save();

	let err = routing_setup();
	if (err)
		return fail(`routing: ${err}`);

	for (let s in req.stations) {
		err = run_all([
			['iw', 'phy', req.phy, 'interface', 'add', s.ifname, 'type', 'managed'],
			['ip', 'link', 'set', s.ifname, 'address', s.mac],
		]);
		if (err)
			return fail(`station ${s.index} of ${count}: ${err}`);
		station_sysctls(s.ifname);
		err = run_all([['ip', 'link', 'set', s.ifname, 'up']]);
		if (err)
			return fail(`station ${s.index} of ${count}: ${err}`);
		run(['iw', 'dev', s.ifname, 'set', 'power_save', 'off']);
	}
	st.created_s = since(t0);
	st.phase = 'associating';
	save();

	// DHCP for connected stations without a lease, at most DHCP_PARALLEL at once
	let dhcp = {};
	let tick = () => {
		let running = {}, connected = 0, leases = 0;
		for (let ifn, pid in dhcp)
			if (pid_alive(pid))
				running[ifn] = pid;
		dhcp = running;
		let busy = length(keys(dhcp));
		for (let s in req.stations) {
			let up = sysfs(`/sys/class/net/${s.ifname}/operstate`) == 'up';
			let leased = access(`${dir}/lease-${s.ifname}.json`) == true;
			if (up)
				connected++;
			if (leased)
				leases++;
			if (up && !leased && dhcp[s.ifname] == null && busy < DHCP_PARALLEL) {
				let pid = spawn(['udhcpc', '-f', '-q', '-n', '-t', '5', '-T', '3', '-i', s.ifname,
					'-x', `hostname:wprobe-${n}-${s.index}`, '-s', DHCP_HOOK], `${dir}/udhcpc.log`);
				if (pid != null) {
					dhcp[s.ifname] = pid;
					busy++;
					writefile(`${dir}/udhcpc-${s.ifname}.pid`, `${pid}\n`);
				}
			}
		}
		st.connected = connected;
		st.leases = leases;
		if (connected > 0 && st.first_connected_s == null)
			st.first_connected_s = since(t0);
		if (connected == count && st.all_connected_s == null)
			st.all_connected_s = since(t0);
		if (leases == count && st.all_leases_s == null)
			st.all_leases_s = since(t0);
		save();
	};

	let batch = [], b = 0;
	let start_batch = () => {
		let argv = ['wpa_supplicant', '-B', '-P', `${dir}/wpas-${b}.pid`, '-D', 'nl80211'];
		for (let k, s in batch) {
			if (k > 0)
				push(argv, '-N');
			push(argv, '-i', s.ifname, '-c', `${dir}/${s.ifname}.conf`);
		}
		let r = run(argv);
		b++;
		batch = [];
		return r.rc == 0 ? null : `wpa_supplicant batch ${b - 1}: ${trim(r.out)} (rc ${r.rc})`;
	};
	for (let s in req.stations) {
		push(batch, s);
		if (length(batch) < req.batch)
			continue;
		err = start_batch();
		if (err)
			return fail(err);
		sleep(req.stagger_ms);
		tick();
	}
	if (length(batch)) {
		err = start_batch();
		if (err)
			return fail(err);
	}
	st.supplicants = b;
	st.phase = 'running';

	// Until sta_down: it kills this process before removing request.json
	while (access(`${dir}/request.json`)) {
		tick();
		sleep(1000);
	}
	return 0;
}

// --- exported calls -----------------------------------------------------------------

function sta_up(a) {
	let band = a.band;
	if (BANDS[band] == null)
		return { error: `band: one of ${join(', ', keys(BANDS))}` };
	let count = a.count ?? 1;
	if (type(count) != 'int' || count < 1 || count > MAX_STATIONS)
		return { error: `count: 1..${MAX_STATIONS}` };
	if (type(a.ssid) != 'string' || length(a.ssid) < 1 || length(a.ssid) > 32)
		return { error: 'ssid: 1..32 bytes' };
	let enc = a.encryption ?? 'psk2';
	if (ENCRYPTIONS[enc] == null)
		return { error: `encryption: one of ${join(', ', keys(ENCRYPTIONS))}` };
	if (enc != 'none' && (type(a.key) != 'string' || length(a.key) < 8 || length(a.key) > 63
	                      || match(a.key, /[\n"]/)))
		return { error: 'key: 8..63 characters, no quotes or newlines' };
	if (a.bssid != null && !match(a.bssid, /^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$/))
		return { error: `bssid ${a.bssid} is not a MAC address` };
	if (a.freq != null && (type(a.freq) != 'int' || a.freq < 2400 || a.freq > 7125))
		return { error: 'freq: MHz' };
	let batch = a.batch ?? DEFAULT_BATCH;
	let stagger_ms = a.stagger_ms ?? DEFAULT_STAGGER_MS;
	if (type(batch) != 'int' || batch < 1 || type(stagger_ms) != 'int' || stagger_ms < 0)
		return { error: 'batch >= 1, stagger_ms >= 0' };

	let phy = phy_for_band(band);
	if (phy == null)
		return { error: `the probe has no ${band} radio` };
	let dir = phy_dir(phy);
	if (access(`${dir}/request.json`))
		return { error: `${band} stations are up (${phy}): call sta_down first`, already_up: true };
	let devs = phy_netdevs(phy);
	if (length(devs.others))
		return { error: `${phy} has other interfaces (${join(' ', devs.others)}): disable its radio in /etc/config/wireless`, busy: devs.others };
	if (length(devs.ours))
		return { error: `${phy} has leftover stations (${join(' ', devs.ours)}): call sta_down` };

	let n = phy_index(phy);
	let stations = [];
	for (let i = 0; i < count; i++) {
		let mac = station_mac(phy, i);
		if (mac == null)
			return { error: `no MAC address for ${phy}` };
		push(stations, { index: i, ifname: station_name(n, i), mac });
	}
	let req = {
		phy, band, ssid: a.ssid, bssid: a.bssid ? lc(a.bssid) : null, freq: a.freq,
		encryption: enc, key: enc == 'none' ? null : a.key, hidden: !!a.hidden,
		batch, stagger_ms, stations,
	};

	mkdir(RUN_DIR);
	mkdir(dir);
	let conf = wpa_conf(req);
	for (let s in stations)
		if (!writefile(`${dir}/${s.ifname}.conf`, conf))
			return { error: `writing ${dir}/${s.ifname}.conf: ${fs_error()}` };
	save_json(`${dir}/request.json`, req);
	let pid = spawn([`${LIBEXEC}/wifi-sta`, dir], `${dir}/helper.log`);
	if (pid == null) {
		run(['rm', '-rf', dir]);
		return { error: `starting ${LIBEXEC}/wifi-sta failed` };
	}
	writefile(`${dir}/helper.pid`, `${pid}\n`);
	return {
		phy, band, count, started: true,
		stations: map(stations, s => ({ index: s.index, ifname: s.ifname, mac: s.mac })),
	};
}

// `iw dev <ifname> link`: bssid, freq, signal, tx bitrate
function station_link(ifn) {
	let r = run(['iw', 'dev', ifn, 'link']);
	let out = {};
	let m = match(r.out, /Connected to ([0-9a-f:]{17})/);
	if (m)
		out.bssid = m[1];
	m = match(r.out, /freq: ([0-9]+)/);
	if (m)
		out.freq = int(m[1]);
	m = match(r.out, /signal: (-?[0-9]+) dBm/);
	if (m)
		out.signal = int(m[1]);
	m = match(r.out, /tx bitrate: ([0-9.]+) MBit\/s/);
	if (m)
		out.tx_mbps = +m[1];
	return out;
}

function sta_status(phy, detail) {
	let dir = phy_dir(phy);
	let req = load(phy);
	if (req == null)
		return { error: `no stations on ${phy}`, not_up: true };
	let st = read_json(`${dir}/state.json`) ?? { phase: 'starting' };
	let helper = read_pids(`${dir}/helper.pid`)[0];
	if (st.phase != 'failed' && !pid_alive(helper)) {
		st.phase = 'failed';
		st.error = `helper exited: ${trim(readfile(`${dir}/helper.log`) ?? '')}`;
	}
	let stations = [], connected = 0, leases = 0;
	for (let s in req.stations) {
		let up = sysfs(`/sys/class/net/${s.ifname}/operstate`) == 'up';
		let cc = sysfs(`/sys/class/net/${s.ifname}/carrier_changes`);
		let lease = read_json(`${dir}/lease-${s.ifname}.json`);
		let e = {
			index: s.index, ifname: s.ifname, mac: s.mac, connected: up,
			carrier_changes: is_uint(cc) ? int(cc) : null,
			ip: lease?.ip, lease,
		};
		if (detail && up)
			e.link = station_link(s.ifname);
		if (up)
			connected++;
		if (lease)
			leases++;
		push(stations, e);
	}
	let res = {
		...st,
		phy, band: req.band, ssid: req.ssid, bssid: req.bssid, freq: req.freq,
		encryption: req.encryption, count: length(req.stations),
		connected, leases, mem_available_kb: mem_available_kb(), stations,
	};
	// Why connected stations have no lease (udhcpc's own messages)
	if (leases < connected)
		res.dhcp_log = tail(`${dir}/udhcpc.log`, 20);
	return res;
}

function ping_stats(out) {
	let tx = match(out, /([0-9]+) packets transmitted/);
	let rx = match(out, /([0-9]+) packets received/);
	return { transmitted: tx ? int(tx[1]) : 0, received: rx ? int(rx[1]) : 0 };
}

// The kernel's IP/ICMP counters (/proc/net/snmp, /proc/net/netstat):
// { 'Ip.InAddrErrors': n, 'Icmp.InEchoReps': n, ... }
function snmp_counters() {
	let out = {};
	for (let file in ['/proc/net/snmp', '/proc/net/netstat']) {
		let lines = split(trim(readfile(file) ?? ''), '\n');
		for (let i = 0; i + 1 < length(lines); i += 2) {
			let names = split(lines[i], /[[:space:]]+/), values = split(lines[i + 1], /[[:space:]]+/);
			let group = rtrim(names[0], ':');
			for (let k = 1; k < length(names); k++)
				if (is_uint(values[k]))
					out[`${group}.${names[k]}`] = int(values[k]);
		}
	}
	return out;
}

// Counters that changed between two snmp_counters()
function counters_diff(before, after) {
	let out = {};
	for (let k, v in after)
		if (v != (before[k] ?? 0))
			out[k] = v - (before[k] ?? 0);
	return out;
}

// Why a ping got nothing back, from the probe's side: counter changes, the
// kernel's martian messages, neighbours and routes of the stations involved
function ping_diag(jobs, before, dmesg_mark) {
	let neigh = {}, routes = {};
	for (let j in jobs) {
		if (!j.ifname)
			continue;
		neigh[j.ifname] = trim(run(['ip', '-4', 'neigh', 'show', 'dev', j.ifname]).out);
		let ids = station_ids(j.ifname);
		routes[j.ifname] = trim(run(['ip', 'route', 'show', 'table', '' + table_id(ids[0], ids[1])]).out);
	}
	let dmesg = split(trim(run(['dmesg']).out), '\n');
	return {
		counters: counters_diff(before, snmp_counters()),
		dmesg: join('\n', slice(dmesg, dmesg_mark)),
		rules: trim(run(['ip', 'rule', 'show']).out),
		neigh, routes,
	};
}

// Ping host from every leased station (or those in `indexes`), all at once.
// diag: also report the probe-side counters and state (ping_diag()).
function sta_ping(phy, host, count, size, indexes, diag) {
	let dir = phy_dir(phy);
	let req = load(phy);
	if (req == null)
		return { error: `no stations on ${phy}`, not_up: true };
	if (!is_ipv4(host))
		return { error: `host: an IPv4 address, not ${host}` };
	count = (type(count) == 'int' && count >= 1) ? min(count, MAX_PING_COUNT) : 3;
	let want = length(indexes ?? []) ? indexes : null;

	let jobs = [], parts = [];
	for (let s in req.stations) {
		if (want && index(want, s.index) < 0)
			continue;
		let lease = read_json(`${dir}/lease-${s.ifname}.json`);
		if (lease == null) {
			push(jobs, { index: s.index, ifname: s.ifname, ip: null, rc: -1, transmitted: 0, received: 0, error: 'no lease' });
			continue;
		}
		let out = `${dir}/ping-${s.ifname}.out`;
		let argv = ['ping', '-c', '' + count, '-W', '2', '-I', lease.ip];
		if (type(size) == 'int' && size >= 0 && size <= 65000)
			push(argv, '-s', '' + size);
		push(argv, host);
		push(parts, `( ${join(' ', map(argv, shq))} >${shq(out)} 2>&1; echo "rc=$?" >>${shq(out)} ) &`);
		push(jobs, { index: s.index, ifname: s.ifname, ip: lease.ip, out });
	}
	let before = diag ? snmp_counters() : null;
	let dmesg_mark = diag ? length(split(trim(run(['dmesg']).out), '\n')) : 0;
	if (length(parts)) {
		let p = popen(join('\n', [...parts, 'wait', '']));
		if (p == null)
			return { error: `ping: ${fs_error()}` };
		p.read('all');
		p.close();
	}
	let results = [], ok = 0;
	for (let j in jobs) {
		if (j.out) {
			let text = readfile(j.out) ?? '';
			unlink(j.out);
			let rc = match(text, /rc=([0-9]+)/);
			j = { ...j, ...ping_stats(text), rc: rc ? int(rc[1]) : -1 };
			delete j.out;
		}
		if (j.received > 0)
			ok++;
		push(results, j);
	}
	let res = { host, count, ok, results };
	if (diag)
		res.diag = ping_diag(results, before, dmesg_mark);
	return res;
}

function sta_down_phy(phy) {
	let dir = phy_dir(phy);
	let n = phy_index(phy);
	let req = load(phy);
	// The helper first, so it starts no more udhcpc
	kill_pidfile(`${dir}/helper.pid`);
	for (let f in lsdir(dir) ?? [])
		if (match(f, /^(wpas-[0-9]+|udhcpc-.*)\.pid$/))
			kill_pidfile(`${dir}/${f}`);
	let indexes = {};
	for (let s in req?.stations ?? [])
		indexes[s.index] = true;
	for (let dev in phy_netdevs(phy).ours)
		indexes[station_ids(dev)[1]] = true;
	for (let i in keys(indexes)) {
		station_routes_del(n, int(i));
		run(['iw', 'dev', station_name(n, i), 'del']);
	}
	// Keep the last run's state and logs for a look afterwards
	mkdir(LAST_DIR);
	run(['rm', '-rf', `${LAST_DIR}/${phy}`]);
	if (access(dir))
		run(['mv', dir, `${LAST_DIR}/${phy}`]);
	return phy;
}

// band: that band's stations; null: all
function sta_down(band) {
	let down = [];
	for (let phy in lsdir(RUN_DIR) ?? [])
		if (phy_index(phy) != null && (band == null || load(phy)?.band == band || load(phy) == null))
			push(down, sta_down_phy(phy));
	// Stations left over without state (rpcd restarted, /var/run cleared)
	if (band == null)
		for (let phy in phys())
			if (length(phy_netdevs(phy).ours))
				push(down, sta_down_phy(phy));
	if (!length(filter(lsdir(RUN_DIR) ?? [], p => phy_index(p) != null)))
		routing_restore();
	return { ok: true, down };
}

export {
	radios, phy_up, sta_up, sta_status, sta_ping, sta_down,
	helper_run, dhcp_hook
};
