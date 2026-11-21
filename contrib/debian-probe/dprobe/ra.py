# SPDX-License-Identifier: LGPL-2.1-only
"""ra-capture: collect and decode Router Advertisements on an interface.

Stdlib only: a raw ICMPv6 socket filtered to RAs (the kernel still does its
own SLAAC on the side), an optional Router Solicitation to trigger one, and a
pure decoder for the RA body that is unit-tested on bytes.
"""

import ipaddress
import socket
import struct
import time

ND_ROUTER_SOLICIT = 133
ND_ROUTER_ADVERT = 134
ICMP6_FILTER = 1  # linux/icmpv6.h
PREFERENCE = {0: "medium", 1: "high", 3: "low", 2: "reserved"}


def _dns_names(raw):
    """Decode the DNS-encoded, zero-padded name list of a DNSSL option."""
    names, labels, i = [], [], 0
    while i < len(raw):
        length = raw[i]
        i += 1
        if length == 0:
            if labels:
                names.append(".".join(labels))
                labels = []
            continue
        labels.append(raw[i : i + length].decode("ascii", "replace"))
        i += length
    return names


def decode_ra(body):
    """Decode an ICMPv6 RA (starting at the ICMPv6 type byte) into a dict.

    Unknown options are listed by type in `other_options`. Raises ValueError
    on a message that isn't an RA or is truncated.
    """
    if len(body) < 16 or body[0] != ND_ROUTER_ADVERT:
        raise ValueError("not a router advertisement")
    hop_limit, flags, lifetime, reachable, retrans = struct.unpack("!BBHII", body[4:16])
    ra = {
        "cur_hop_limit": hop_limit,
        "managed": bool(flags & 0x80),
        "other": bool(flags & 0x40),
        "home_agent": bool(flags & 0x20),
        "preference": PREFERENCE[(flags >> 3) & 0x3],
        "proxy": bool(flags & 0x04),
        "router_lifetime": lifetime,
        "reachable_time": reachable,
        "retrans_timer": retrans,
        "source_lla": None,
        "mtu": None,
        "prefixes": [],
        "routes": [],
        "rdnss": [],
        "dnssl": [],
        "other_options": [],
    }
    i = 16
    while i < len(body):
        if i + 2 > len(body):
            raise ValueError("truncated option header")
        otype, olen = body[i], body[i + 1] * 8
        if olen == 0 or i + olen > len(body):
            raise ValueError(f"bad length of option {otype} at {i}")
        opt = body[i : i + olen]
        if otype == 1 and olen >= 8:
            ra["source_lla"] = ":".join(f"{b:02x}" for b in opt[2:8])
        elif otype == 3 and olen == 32:
            plen, pflags, valid, preferred = struct.unpack("!BBII", opt[2:12])
            ra["prefixes"].append(
                {
                    "prefix": f"{ipaddress.IPv6Address(opt[16:32])}/{plen}",
                    "on_link": bool(pflags & 0x80),
                    "autonomous": bool(pflags & 0x40),
                    "router_address": bool(pflags & 0x20),
                    "valid": valid,
                    "preferred": preferred,
                }
            )
        elif otype == 5 and olen == 8:
            ra["mtu"] = struct.unpack("!I", opt[4:8])[0]
        elif otype == 24 and olen >= 8:
            plen, rflags, rlifetime = struct.unpack("!BBI", opt[2:8])
            prefix = (opt[8:] + bytes(16))[:16]
            ra["routes"].append(
                {
                    "prefix": f"{ipaddress.IPv6Address(prefix)}/{plen}",
                    "preference": PREFERENCE[(rflags >> 3) & 0x3],
                    "lifetime": rlifetime,
                }
            )
        elif otype == 25 and olen >= 24:
            rlifetime = struct.unpack("!I", opt[4:8])[0]
            for j in range(8, olen - 15, 16):
                ra["rdnss"].append(
                    {
                        "address": str(ipaddress.IPv6Address(opt[j : j + 16])),
                        "lifetime": rlifetime,
                    }
                )
        elif otype == 31 and olen >= 8:
            dlifetime = struct.unpack("!I", opt[4:8])[0]
            ra["dnssl"] += [
                {"domain": name, "lifetime": dlifetime} for name in _dns_names(opt[8:])
            ]
        else:
            ra["other_options"].append(otype)
        i += olen
    return ra


def build_rs(mac=None):
    """ICMPv6 Router Solicitation; the kernel fills in the checksum."""
    body = struct.pack("!BBHI", ND_ROUTER_SOLICIT, 0, 0, 0)
    if mac:
        body += bytes([1, 1]) + bytes.fromhex(mac.replace(":", ""))
    return body


def _icmp6_filter(*allowed):
    words = [0xFFFFFFFF] * 8  # a set bit blocks that type
    for t in allowed:
        words[t >> 5] &= ~(1 << (t & 31))
    return struct.pack("8I", *words)


def capture(iface, duration=10.0, count=None, solicit=True, mac=None):
    """Collect RAs on iface for up to `duration` s (or until `count`).

    Returns [{t, source, hop_limit, ...decode_ra()}]; t is seconds since the
    capture started. RAs whose IPv6 hop limit isn't 255 are kept but marked,
    since hosts must ignore them.
    """
    ifindex = socket.if_nametoindex(iface)
    sock = socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_ICMPV6)
    ras = []
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, iface.encode())
        sock.setsockopt(
            socket.IPPROTO_ICMPV6, ICMP6_FILTER, _icmp6_filter(ND_ROUTER_ADVERT)
        )
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_RECVHOPLIMIT, 1)
        start = time.monotonic()
        if solicit:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, 255)
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, ifindex)
            sock.sendto(build_rs(mac), ("ff02::2", 0, 0, ifindex))
        while count is None or len(ras) < count:
            left = start + duration - time.monotonic()
            if left <= 0:
                break
            sock.settimeout(left)
            try:
                data, ancdata, _flags, addr = sock.recvmsg(65535, socket.CMSG_SPACE(4))
            except TimeoutError:
                break
            hop_limit = None
            for level, ctype, cdata in ancdata:
                if level == socket.IPPROTO_IPV6 and ctype == socket.IPV6_HOPLIMIT:
                    hop_limit = struct.unpack("i", cdata[:4])[0]
            try:
                ra = decode_ra(data)
            except ValueError as e:
                ras.append({"t": round(time.monotonic() - start, 2), "error": str(e)})
                continue
            ras.append(
                {
                    "t": round(time.monotonic() - start, 2),
                    "source": addr[0].split("%")[0],
                    "hop_limit": hop_limit,
                    "valid_hop_limit": hop_limit in (None, 255),
                    **ra,
                }
            )
    finally:
        sock.close()
    return ras
