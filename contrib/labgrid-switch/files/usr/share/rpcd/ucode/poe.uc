// rpcd plugin: the ubus object "poe" for switches whose PoE is driven by
// poemgr (Plasma Cloud PSX8/PSX10/PSX28, UniFi Flex). poemgr has no ubus
// interface of its own; this offers the two calls of realtek-poe that
// pdudaemon's ubus driver uses, so such a switch works as a PDU like any
// other:
//
//   ubus call poe info
//   ubus call poe manage '{"port":"lan1","enable":false}'
//
// info    { budget, ports: { lan1: "Disabled" | { status, consumption, ... } } }
//         (a port that is switched off is the string "Disabled", as with
//         realtek-poe: that is what pdudaemon tests)
// manage  sets the port's `disabled` option in /etc/config/poemgr and runs
//         `poemgr apply`, so the state survives a reboot of the switch
//
// Ports are named by the `name` option of their section in /etc/config/poemgr
// (lan1..lanN by default). Not for switches with realtek-poe: its own "poe"
// object would clash with this one. Restart rpcd after changing this file.

'use strict';

import { popen } from 'fs';
import { cursor } from 'uci';

// action is one of our own constants, never caller input
function poemgr(action) {
	let p = popen(`/sbin/poemgr ${action} 2>/dev/null`);
	if (!p)
		return { rc: -1, out: '' };
	let out = p.read('all');
	return { rc: p.close(), out };
}

// The ports of /etc/config/poemgr: [{ section, name, index }]
function ports(ctx) {
	let res = [];
	ctx.foreach('poemgr', 'port', s => {
		if (type(s.port) == 'string' && match(s.port, /^[0-9]+$/))
			push(res, { section: s['.name'], name: s.name ?? s['.name'], index: s.port });
	});
	return res;
}

// `poemgr show` as an object, or null: it fails while the profile is
// disabled (/etc/init.d/poemgr stopped), and then no port has power
function show() {
	let r = poemgr('show');
	if (r.rc != 0)
		return null;
	try {
		return json(r.out);
	}
	catch (e) {
		return null;
	}
}

return {
	poe: {
		info: {
			call: () => {
				let state = show();
				let res = {};
				for (let p in ports(cursor())) {
					let live = state?.output?.ports?.[p.index];
					if (!live?.enabled) {
						res[p.name] = 'Disabled';
						continue;
					}
					res[p.name] = {
						status: live.active ? 'Delivering power' : 'Searching',
						consumption: live.power,
						class: live.poe_class,
						faults: live.faults ?? [],
					};
				}
				return { budget: state?.output?.power_budget, ports: res };
			},
		},
		manage: {
			args: { port: '', enable: true },
			call: req => {
				let a = req.args;
				if (a.port == null || a.enable == null)
					exit(UBUS_STATUS_INVALID_ARGUMENT);

				let ctx = cursor();
				let section = null;
				for (let p in ports(ctx))
					if (p.name == a.port)
						section = p.section;
				if (section == null)
					exit(UBUS_STATUS_NOT_FOUND);

				if (ctx.set('poemgr', section, 'disabled', a.enable ? '0' : '1') == null ||
				    ctx.commit('poemgr') == null)
					exit(UBUS_STATUS_UNKNOWN_ERROR);
				if (poemgr('apply').rc != 0)
					exit(UBUS_STATUS_UNKNOWN_ERROR);
				return { port: a.port, enabled: !!a.enable };
			},
		},
	},
};
