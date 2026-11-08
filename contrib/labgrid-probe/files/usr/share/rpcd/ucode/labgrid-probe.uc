// rpcd plugin: the ubus object "labgrid-probe" (logic in
// /usr/share/ucode/labgrid_probe.uc). Tests call it as JSON-RPC over HTTP
// (uhttpd /ubus, unauthenticated session, see acl.d/labgrid-probe.json):
//
//   ubus call labgrid-probe up '{"lan_vlan":102}'
//   ubus call labgrid-probe wan_up '{"lan_vlan":102,"wan_vlan":101}'
//
// Every method takes lan_vlan; up() creates that slot, the others use it.
// Failures are replies with an "error" string ("not_up": true if up() wasn't
// called). Restart rpcd after changing this file or the module.

'use strict';

import * as probe from 'labgrid_probe';

function need_vlan(req) {
	if (req.args.lan_vlan == null)
		exit(UBUS_STATUS_INVALID_ARGUMENT);
	return req.args.lan_vlan;
}

// Run fn(slot, args) for an existing slot
function with_slot(fn) {
	return req => {
		let d = probe.load(need_vlan(req));
		if (d == null)
			return { error: `lan_vlan ${req.args.lan_vlan} is not set up, call up first`, not_up: true };
		return fn(d, req.args);
	};
}

const VLAN = { lan_vlan: 32 };

return {
	'labgrid-probe': {
		// lan_vlan may be left out on a lab host (local mode) with parent
		up: {
			args: { lan_vlan: 32, trunk: '', parent: '' },
			call: req => probe.up(req.args.lan_vlan, req.args.trunk, req.args.parent),
		},
		down: {
			args: VLAN,
			call: req => probe.down(need_vlan(req)),
		},
		info: {
			args: VLAN,
			call: req => {
				let d = probe.load(need_vlan(req));
				return d ? probe.info(d) : { lan_vlan: req.args.lan_vlan, not_up: true };
			},
		},
		wan_up: {
			args: { lan_vlan: 32, wan_vlan: 32, wan_ip: '', wan_parent: '' },
			call: with_slot((d, a) => probe.wan_up(d, a.wan_vlan, a.wan_ip, a.wan_parent)),
		},
		dhcp: {
			args: VLAN,
			call: with_slot(d => probe.dhcp(d)),
		},
		addresses: {
			args: VLAN,
			call: with_slot(d => probe.addresses(d)),
		},
		ping: {
			args: { lan_vlan: 32, host: '', count: 32 },
			call: with_slot((d, a) => probe.ping(d, a.host ?? '192.168.1.1', a.count)),
		},
		resolve: {
			args: { lan_vlan: 32, name: '', server: '' },
			call: with_slot((d, a) => {
				if (a.name == null)
					exit(UBUS_STATUS_INVALID_ARGUMENT);
				return probe.resolve(d, a.name, a.server ?? '192.168.1.1');
			}),
		},
		wan_dhcp: {
			args: { lan_vlan: 32, options: [], ntp: true },
			call: with_slot((d, a) => probe.wan_dhcp(d, a.options, a.ntp)),
		},
		speedtest_start: {
			args: { lan_vlan: 32, duration: 32, reverse: true, streams: 32, udp: true,
			        bitrate: '', server: '', port: 32 },
			call: with_slot((d, a) => probe.speedtest_start(d, a)),
		},
		speedtest_result: {
			args: VLAN,
			call: with_slot(d => probe.speedtest_result(d)),
		},
		ra_capture: {
			args: { lan_vlan: 32, timeout: 32, solicit: true, count: 32 },
			call: with_slot((d, a) => probe.ra_capture(d, a.timeout, a.solicit, a.count)),
		},
		mcast_join: {
			args: { lan_vlan: 32, group: '', port: 32, side: '', igmp_version: 32 },
			call: with_slot((d, a) => probe.mcast_join(d, a.group, a.port, a.side, a.igmp_version)),
		},
		mcast_send: {
			args: { lan_vlan: 32, group: '', port: 32, side: '', count: 32, interval: 32, ttl: 32 },
			call: with_slot((d, a) => probe.mcast_send(d, a.group, a.port, a.side, a.count,
			                                           a.interval, a.ttl)),
		},
		mcast_result: {
			args: VLAN,
			call: with_slot(d => probe.mcast_result(d)),
		},
		mcast_leave: {
			args: VLAN,
			call: with_slot(d => probe.mcast_leave(d)),
		},
	}
};
