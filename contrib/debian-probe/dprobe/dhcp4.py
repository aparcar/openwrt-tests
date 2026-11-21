# SPDX-License-Identifier: LGPL-2.1-only
"""dhcp4 test client: DISCOVER/REQUEST with scapy, parsed options.

The client MAC can be synthetic (--mac): replies are requested as broadcast,
so they arrive whatever the interface's own MAC. decode() and lease_from()
work on scapy packets and are unit-tested without a network.
"""

import ipaddress
import random
import time

MESSAGE_TYPES = {
    1: "DISCOVER",
    2: "OFFER",
    3: "REQUEST",
    4: "DECLINE",
    5: "ACK",
    6: "NAK",
    7: "RELEASE",
    8: "INFORM",
}

# subnet, router, dns, hostname, domain, broadcast, ntp, lease, server id,
# T1, T2: roughly what udhcpc asks for
DEFAULT_PARAMS = [1, 3, 6, 12, 15, 28, 42, 51, 54, 58, 59]


def _value(v):
    if isinstance(v, bytes):
        try:
            text = v.decode("ascii")
            if text.isprintable():
                return text
        except UnicodeDecodeError:
            pass
        return v.hex()
    return v


def decode(pkt):
    """BOOTP/DHCP reply (scapy packet) -> dict with every option.

    options: [{code, name, value}] in wire order; value is a scalar or a
    list for multi-value options, bytes become text or hex.
    """
    from scapy.layers.dhcp import BOOTP, DHCP, DHCPRevOptions

    bootp = pkt[BOOTP]
    options = []
    message_type = None
    for opt in pkt[DHCP].options if DHCP in pkt else []:
        if not isinstance(opt, tuple):
            continue  # "end", "pad"
        name, *values = opt
        if isinstance(name, int):
            code, name = name, None
        else:
            code = DHCPRevOptions.get(name, (None,))[0]
        values = [_value(v) for v in values]
        value = values[0] if len(values) == 1 else values
        if code == 53:
            message_type = MESSAGE_TYPES.get(value, value)
        options.append({"code": code, "name": name, "value": value})
    return {
        "message_type": message_type,
        "xid": bootp.xid,
        "yiaddr": bootp.yiaddr,
        "siaddr": bootp.siaddr,
        "chaddr": ":".join(f"{b:02x}" for b in bytes(bootp.chaddr)[:6]),
        "options": options,
    }


def option(reply, code):
    """Value of option `code` in a decode() result, or None."""
    for opt in reply["options"]:
        if opt["code"] == code:
            return opt["value"]
    return None


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def lease_from(reply):
    """The usual fields of an ACK (or OFFER) from decode()."""
    mask = option(reply, 1)
    prefixlen = ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen if mask else None
    return {
        "ip": reply["yiaddr"],
        "mask": mask,
        "prefixlen": prefixlen,
        "broadcast": option(reply, 28),
        "router": _as_list(option(reply, 3)),
        "dns": _as_list(option(reply, 6)),
        "ntp": _as_list(option(reply, 42)),
        "domain": option(reply, 15),
        "lease_time": option(reply, 51),
        "renewal_time": option(reply, 58),
        "rebinding_time": option(reply, 59),
        "server_id": option(reply, 54),
    }


def _packet(mac, xid, message_type, params, hostname=None, extra=()):
    from scapy.layers.dhcp import BOOTP, DHCP
    from scapy.layers.inet import IP, UDP
    from scapy.layers.l2 import Ether
    from scapy.utils import mac2str

    options = [("message-type", message_type), ("param_req_list", params)]
    if hostname:
        options.append(("hostname", hostname.encode()))
    options += list(extra)
    options.append("end")
    return (
        Ether(src=mac, dst="ff:ff:ff:ff:ff:ff")
        / IP(src="0.0.0.0", dst="255.255.255.255")
        / UDP(sport=68, dport=67)
        / BOOTP(chaddr=mac2str(mac), xid=xid, flags=0x8000)
        / DHCP(options=options)
    )


def _wait(sniffer, xid, types, deadline, settle=0.0):
    """Replies with this xid of the wanted types. Returns once one arrived
    (plus `settle` s to catch other servers) or at the deadline."""
    from scapy.layers.dhcp import BOOTP

    first = None
    while True:
        now = time.monotonic()
        got = [
            p
            for p in sniffer.results or []
            if BOOTP in p and p[BOOTP].op == 2 and p[BOOTP].xid == xid
        ]
        decoded = [decode(p) for p in got]
        decoded = [d for d in decoded if d["message_type"] in types]
        if decoded and first is None:
            first = now
        if now >= deadline or (first is not None and now - first >= settle):
            return decoded
        time.sleep(0.05)


def exchange(
    iface,
    mac,
    timeout=10.0,
    request=True,
    server=None,
    hostname=None,
    params=None,
):
    """DISCOVER, collect OFFERs, then REQUEST the chosen one.

    server: only accept offers with this server id (e.g. the DUT, when
    another DHCP server might answer on the VLAN).
    Returns {offers, ack, nak, lease}; lease is from the ACK (or the
    chosen offer if request=False), None if nothing usable came back.
    """
    from scapy.sendrecv import AsyncSniffer, sendp

    params = params or DEFAULT_PARAMS
    xid = random.randint(1, 0xFFFFFFFF)
    sniffer = AsyncSniffer(
        iface=iface,
        store=True,
        lfilter=lambda p: p.haslayer("BOOTP") and p["BOOTP"].op == 2,
    )
    sniffer.start()
    try:
        time.sleep(0.2)  # let the sniffer attach
        deadline = time.monotonic() + timeout
        sendp(
            _packet(mac, xid, "discover", params, hostname), iface=iface, verbose=False
        )
        offers = _wait(sniffer, xid, {"OFFER"}, deadline, settle=0.5)
        chosen = [o for o in offers if server is None or option(o, 54) == server]
        result = {"xid": xid, "mac": mac, "offers": offers, "ack": None, "nak": None}
        if not chosen:
            result["lease"] = None
            return result
        offer = chosen[0]
        if not request:
            result["lease"] = lease_from(offer)
            return result
        extra = [
            ("requested_addr", offer["yiaddr"]),
            ("server_id", option(offer, 54)),
        ]
        deadline = time.monotonic() + timeout
        sendp(
            _packet(mac, xid, "request", params, hostname, extra),
            iface=iface,
            verbose=False,
        )
        replies = _wait(sniffer, xid, {"ACK", "NAK"}, deadline)
        for r in replies:
            result[r["message_type"].lower()] = r
        result["lease"] = lease_from(result["ack"]) if result["ack"] else None
        return result
    finally:
        sniffer.stop()
