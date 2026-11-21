# SPDX-License-Identifier: LGPL-2.1-only
"""dhcp6 test client: SOLICIT (+ REQUEST) for IA_NA and/or IA_PD.

Messages are built with scapy and sent on a UDP socket bound to port 546 on
the interface, so the kernel handles IPv6/UDP. decode() takes the raw DHCPv6
message bytes and is unit-tested without a network.
"""

import random
import socket
import struct
import time

ALL_SERVERS = "ff02::1:2"
STATUS = {
    0: "Success",
    1: "UnspecFail",
    2: "NoAddrsAvail",
    3: "NoBinding",
    4: "NotOnLink",
    5: "UseMulticast",
    6: "NoPrefixAvail",
}
IAID_NA = 1
IAID_PD = 2


def _status(opts):
    from scapy.layers.dhcp6 import DHCP6OptStatusCode

    for o in opts or []:
        if isinstance(o, DHCP6OptStatusCode):
            return {
                "code": STATUS.get(o.statuscode, o.statuscode),
                "message": o.statusmsg,
            }
    return None


def _options(msg):
    """The top-level options of a scapy DHCPv6 message, in order."""
    from scapy.packet import NoPayload

    out = []
    o = msg.payload
    while o is not None and not isinstance(o, NoPayload):
        out.append(o)
        o = o.payload
    return out


def decode(data):
    """Raw DHCPv6 message -> dict (message type, server DUID, IAs, DNS)."""
    from scapy.layers import dhcp6 as d6

    if not data:
        raise ValueError("empty DHCPv6 message")
    name = d6.dhcp6_cls_by_type.get(data[0])
    if name is None:
        raise ValueError(f"unknown DHCPv6 message type {data[0]}")
    msg = getattr(d6, name)(data)
    result = {
        "message_type": name.removeprefix("DHCP6_").upper(),
        "trid": getattr(msg, "trid", None),
        "server_duid": None,
        "client_duid": None,
        "preference": None,
        "status": None,
        "rapid_commit": False,
        "reconfigure_accept": False,
        "ia_na": [],
        "ia_pd": [],
        "dns": [],
        "domains": [],
        "other_options": [],
    }
    for o in _options(msg):
        if isinstance(o, d6.DHCP6OptServerId):
            result["server_duid"] = bytes(o.duid).hex()
        elif isinstance(o, d6.DHCP6OptClientId):
            result["client_duid"] = bytes(o.duid).hex()
        elif isinstance(o, d6.DHCP6OptPref):
            result["preference"] = o.prefval
        elif isinstance(o, d6.DHCP6OptStatusCode):
            result["status"] = _status([o])
        elif isinstance(o, d6.DHCP6OptRapidCommit):
            result["rapid_commit"] = True
        elif isinstance(o, d6.DHCP6OptReconfAccept):
            result["reconfigure_accept"] = True
        elif isinstance(o, d6.DHCP6OptIA_NA):
            result["ia_na"].append(
                {
                    "iaid": o.iaid,
                    "t1": o.T1,
                    "t2": o.T2,
                    "status": _status(o.ianaopts),
                    "addresses": [
                        {
                            "address": a.addr,
                            "preferred": a.preflft,
                            "valid": a.validlft,
                            "status": _status(a.iaaddropts),
                        }
                        for a in o.ianaopts
                        if isinstance(a, d6.DHCP6OptIAAddress)
                    ],
                }
            )
        elif isinstance(o, d6.DHCP6OptIA_PD):
            result["ia_pd"].append(
                {
                    "iaid": o.iaid,
                    "t1": o.T1,
                    "t2": o.T2,
                    "status": _status(o.iapdopt),
                    "prefixes": [
                        {
                            "prefix": f"{p.prefix}/{p.plen}",
                            "preferred": p.preflft,
                            "valid": p.validlft,
                            "status": _status(p.iaprefopts),
                        }
                        for p in o.iapdopt
                        if isinstance(p, d6.DHCP6OptIAPrefix)
                    ],
                }
            )
        elif isinstance(o, d6.DHCP6OptDNSServers):
            result["dns"] += list(o.dnsservers)
        elif isinstance(o, d6.DHCP6OptDNSDomains):
            result["domains"] += [d.rstrip(".") for d in o.dnsdomains]
        else:
            result["other_options"].append(getattr(o, "optcode", None))
    return result


def build(kind, trid, mac, na=True, pd=True, pd_len=None, server_duid=None, ias=None):
    """SOLICIT or REQUEST bytes. `ias` (from an ADVERTISE) are echoed back
    in a REQUEST so the server commits exactly what it offered."""
    from scapy.layers import dhcp6 as d6

    cls = {"solicit": d6.DHCP6_Solicit, "request": d6.DHCP6_Request}[kind]
    msg = (
        cls(trid=trid)
        / d6.DHCP6OptClientId(duid=d6.DUID_LL(lladdr=mac))
        / d6.DHCP6OptElapsedTime()
        / d6.DHCP6OptOptReq(reqopts=[23, 24])
    )
    if server_duid:
        # echo the server's DUID verbatim, whatever its type (kea: DUID-LLT)
        duid = bytes.fromhex(server_duid)
        msg /= d6.DHCP6OptServerId(struct.pack("!HH", 2, len(duid)) + duid)
    if na:
        addrs = []
        for ia in (ias or {}).get("ia_na", []):
            addrs += [d6.DHCP6OptIAAddress(addr=a["address"]) for a in ia["addresses"]]
        msg /= d6.DHCP6OptIA_NA(iaid=IAID_NA, ianaopts=addrs)
    if pd:
        prefixes = []
        for ia in (ias or {}).get("ia_pd", []):
            for p in ia["prefixes"]:
                prefix, plen = p["prefix"].split("/")
                prefixes.append(d6.DHCP6OptIAPrefix(prefix=prefix, plen=int(plen)))
        if not prefixes and pd_len:
            prefixes = [d6.DHCP6OptIAPrefix(prefix="::", plen=pd_len)]
        msg /= d6.DHCP6OptIA_PD(iaid=IAID_PD, iapdopt=prefixes)
    return bytes(msg)


def _collect(sock, trid, kinds, deadline, settle):
    replies, first = [], None
    while True:
        now = time.monotonic()
        if now >= deadline or (first is not None and now - first >= settle):
            return replies
        end = deadline if first is None else min(deadline, first + settle)
        sock.settimeout(max(end - now, 0.01))
        try:
            data, addr = sock.recvfrom(4096)
        except TimeoutError:
            continue
        try:
            reply = decode(data)
        except (ValueError, IndexError):
            continue
        if reply["trid"] != trid or reply["message_type"] not in kinds:
            continue
        reply["from"] = addr[0].split("%")[0]
        replies.append(reply)
        if first is None:
            first = time.monotonic()


def exchange(iface, mac, na=True, pd=True, pd_len=None, request=True, timeout=10.0):
    """SOLICIT, collect ADVERTISEs, then REQUEST from the most preferred one.

    Returns {advertises, reply}; reply is the REPLY to the REQUEST (None
    with request=False or if nothing came back).
    """
    ifindex = socket.if_nametoindex(iface)
    with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, iface.encode())
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("::", 546))
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, ifindex)
        dest = (ALL_SERVERS, 547, 0, ifindex)

        trid = random.randint(1, 0xFFFFFF)
        sock.sendto(build("solicit", trid, mac, na, pd, pd_len), dest)
        advertises = _collect(
            sock, trid, {"ADVERTISE", "REPLY"}, time.monotonic() + timeout, 1.0
        )
        result = {"mac": mac, "advertises": advertises, "reply": None}
        offers = [a for a in advertises if a["message_type"] == "ADVERTISE"]
        if not request or not offers:
            return result
        best = max(offers, key=lambda a: a["preference"] or 0)
        trid = random.randint(1, 0xFFFFFF)
        sock.sendto(
            build("request", trid, mac, na, pd, pd_len, best["server_duid"], best),
            dest,
        )
        replies = _collect(sock, trid, {"REPLY"}, time.monotonic() + timeout, 0)
        result["reply"] = replies[0] if replies else None
        return result
