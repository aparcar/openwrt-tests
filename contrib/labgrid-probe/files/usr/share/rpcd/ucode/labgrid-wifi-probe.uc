// rpcd plugin: the ubus object "labgrid-wifi-probe" (logic in
// /usr/share/ucode/labgrid_probe_wifi.uc). Tests call it as JSON-RPC over
// HTTP (uhttpd /ubus, unauthenticated session, see
// acl.d/labgrid-wifi-probe.json):
//
//   ubus call labgrid-wifi-probe radios
//   ubus call labgrid-wifi-probe sta_up '{"band":"2g","count":32,"ssid":"x","bssid":"..","encryption":"psk2","key":"..."}'
//   ubus call labgrid-wifi-probe sta_status '{"band":"2g"}'
//   ubus call labgrid-wifi-probe sta_ping '{"band":"2g","host":"192.168.1.1"}'
//   ubus call labgrid-wifi-probe sta_down '{}'
//
// One set of stations per band (radio). Failures are replies with an "error"
// string ("not_up": true without sta_up). Restart rpcd after changing this
// file or the module.

'use strict';

import * as wifi from 'labgrid_probe_wifi';

function need_band(req) {
	if (req.args.band == null)
		exit(UBUS_STATUS_INVALID_ARGUMENT);
	return req.args.band;
}

// Run fn(phy, args) for the band's stations
function with_band(fn) {
	return req => {
		let phy = wifi.phy_up(need_band(req));
		if (phy == null)
			return { error: `no ${req.args.band} stations, call sta_up first`, not_up: true };
		return fn(phy, req.args);
	};
}

return {
	'labgrid-wifi-probe': {
		radios: {
			args: {},
			call: () => wifi.radios(),
		},
		sta_up: {
			args: { band: '', count: 32, ssid: '', bssid: '', freq: 32, encryption: '', key: '',
			        hidden: true, batch: 32, stagger_ms: 32 },
			call: req => wifi.sta_up(req.args),
		},
		sta_status: {
			args: { band: '', detail: true },
			call: with_band((phy, a) => wifi.sta_status(phy, a.detail)),
		},
		sta_ping: {
			args: { band: '', host: '', count: 32, size: 32, stations: [], diag: true },
			call: with_band((phy, a) => wifi.sta_ping(phy, a.host, a.count, a.size, a.stations, a.diag)),
		},
		// Without band: every band's stations
		sta_down: {
			args: { band: '' },
			call: req => wifi.sta_down(req.args.band),
		},
	}
};
