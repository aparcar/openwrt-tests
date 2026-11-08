// labgrid-probe: parse an ICMPv6 Router Advertisement (RFC 4861, 4191,
// 8106) as a raw ICMPv6 socket delivers it (no IPv6 header) into an object.
// Used by /usr/libexec/labgrid-probe/ra-capture. Pure: no sockets, so it can
// be checked off-device.

'use strict';

import { unpack } from 'struct';

const ND_ROUTER_ADVERT = 134;

// Router preference (RFC 4191): 2-bit field, 10 is reserved
const PREFERENCE = ['medium', 'high', 'reserved', 'low'];

function ip6(buf, off) {
	return arrtoip(unpack('16B', buf, off));
}

// RFC 1035 labels (no compression, as DNSSL requires), padded with zeros
function domains(buf, off, end) {
	let names = [], cur = [];
	while (off < end) {
		let n = ord(buf, off++);
		if (n == 0) {
			if (length(cur))
				push(names, join('.', cur));
			cur = [];
			continue;
		}
		if (off + n > end)
			break;
		push(cur, substr(buf, off, n));
		off += n;
	}
	return names;
}

// Returns the parsed RA, or null if buf isn't one. Lifetimes are in seconds
// (4294967295 = infinity), reachable_time/retrans_timer in milliseconds.
function parse_ra(buf) {
	if (type(buf) != 'string' || length(buf) < 16 || ord(buf, 0) != ND_ROUTER_ADVERT)
		return null;

	// type, code, checksum, cur hop limit, flags, router lifetime, reachable, retrans
	let h = unpack('!BBHBBHII', buf);
	let flags = h[4];
	let ra = {
		code: h[1],
		hop_limit: h[3],
		managed: (flags & 0x80) != 0,
		other: (flags & 0x40) != 0,
		home_agent: (flags & 0x20) != 0,
		preference: PREFERENCE[(flags >> 3) & 3],
		proxy: (flags & 0x04) != 0,
		router_lifetime: h[5],
		reachable_time: h[6],
		retrans_timer: h[7],
		mtu: null,
		source_lladdr: null,
		prefixes: [],
		routes: [],
		rdnss: [],
		dnssl: [],
		unknown_options: [],
	};

	let off = 16, total = length(buf);
	while (off + 2 <= total) {
		let opt = ord(buf, off), len = ord(buf, off + 1) * 8;
		if (len == 0 || off + len > total) {
			ra.malformed = true;
			break;
		}
		if (opt == 1 && len >= 8) {
			ra.source_lladdr = join(':', map(unpack('6B', buf, off + 2), b => sprintf('%02x', b)));
		}
		else if (opt == 3 && len == 32) {
			// prefix length, flags, valid, preferred, reserved, prefix
			let o = unpack('!BBIII', buf, off + 2);
			push(ra.prefixes, {
				prefix: ip6(buf, off + 16),
				length: o[0],
				on_link: (o[1] & 0x80) != 0,
				autonomous: (o[1] & 0x40) != 0,
				router_address: (o[1] & 0x20) != 0,
				valid_lifetime: o[2],
				preferred_lifetime: o[3],
			});
		}
		else if (opt == 5 && len == 8) {
			ra.mtu = unpack('!I', buf, off + 4)[0];
		}
		else if (opt == 24 && len >= 8 && len <= 24) {
			// prefix length, flags (preference), lifetime, 0/8/16 prefix bytes
			let o = unpack('!BBI', buf, off + 2);
			let bytes = (len > 8) ? unpack(`${len - 8}B`, buf, off + 8) : [];
			while (length(bytes) < 16)
				push(bytes, 0);
			push(ra.routes, {
				prefix: arrtoip(bytes),
				length: o[0],
				preference: PREFERENCE[(o[1] >> 3) & 3],
				lifetime: o[2],
			});
		}
		else if (opt == 25 && len >= 24) {
			let lifetime = unpack('!I', buf, off + 4)[0];
			for (let a = off + 8; a + 16 <= off + len; a += 16)
				push(ra.rdnss, { address: ip6(buf, a), lifetime });
		}
		else if (opt == 31 && len >= 16) {
			let lifetime = unpack('!I', buf, off + 4)[0];
			push(ra.dnssl, { domains: domains(buf, off + 8, off + len), lifetime });
		}
		else {
			push(ra.unknown_options, opt);
		}
		off += len;
	}
	return ra;
}

export { parse_ra };
