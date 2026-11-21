# SPDX-License-Identifier: LGPL-2.1-only
"""dns-pair: A and AAAA queries back to back on one UDP socket.

This is what glibc's resolver does (same 5-tuple, two same-size datagrams
right after each other). On drivers with rx-gro-list (bcmgenet, see
openwrt/openwrt#16261) the two get merged on the DUT and dnsmasq never
answers. Plain stdlib: a DNS codec (pure, unit-tested) and a socket loop.
"""

import ipaddress
import random
import socket
import struct
import time

QTYPE = {"A": 1, "AAAA": 28}
QTYPE_NAME = {v: k for k, v in QTYPE.items()}
RCODE = {
    0: "NOERROR",
    1: "FORMERR",
    2: "SERVFAIL",
    3: "NXDOMAIN",
    4: "NOTIMP",
    5: "REFUSED",
}


def encode_name(name):
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("idna" if not label.isascii() else "ascii")
        if not 0 < len(raw) < 64:
            raise ValueError(f"bad label {label!r} in {name!r}")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def build_query(qid, name, qtype):
    """A standard recursive query without EDNS (like glibc's default)."""
    if isinstance(qtype, str):
        qtype = QTYPE[qtype]
    header = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    return header + encode_name(name) + struct.pack("!HH", qtype, 1)


def _read_name(data, offset):
    """Return (name, offset after the name) with compression pointers."""
    labels = []
    end = None
    seen = 0
    while True:
        if offset >= len(data):
            raise ValueError("name runs past the end of the message")
        length = data[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise ValueError("truncated compression pointer")
            if end is None:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | data[offset + 1]
            seen += 1
            if seen > 32:
                raise ValueError("compression loop")
            continue
        offset += 1
        if length == 0:
            break
        labels.append(data[offset : offset + length].decode("ascii", "replace"))
        offset += length
    return ".".join(labels), (end if end is not None else offset)


def parse_response(data):
    """Parse a DNS response: id, rcode, question type/name and A/AAAA answers."""
    if len(data) < 12:
        raise ValueError(f"short DNS message ({len(data)} bytes)")
    qid, flags, qdcount, ancount, _, _ = struct.unpack("!HHHHHH", data[:12])
    offset = 12
    qname, qtype = None, None
    for _ in range(qdcount):
        qname, offset = _read_name(data, offset)
        qtype, _qclass = struct.unpack("!HH", data[offset : offset + 4])
        offset += 4
    answers = []
    for _ in range(ancount):
        _name, offset = _read_name(data, offset)
        rtype, _rclass, _ttl, rdlen = struct.unpack("!HHIH", data[offset : offset + 10])
        offset += 10
        rdata = data[offset : offset + rdlen]
        offset += rdlen
        if rtype == 1 and rdlen == 4:
            answers.append(str(ipaddress.IPv4Address(rdata)))
        elif rtype == 28 and rdlen == 16:
            answers.append(str(ipaddress.IPv6Address(rdata)))
    return {
        "id": qid,
        "qr": bool(flags & 0x8000),
        "rcode": RCODE.get(flags & 0xF, str(flags & 0xF)),
        "qname": qname,
        "qtype": QTYPE_NAME.get(qtype, qtype),
        "answers": answers,
    }


def summarize(results):
    """Counts over the per-try results of run_pairs()."""
    both = sum(1 for r in results if r["A"] and r["AAAA"])
    none = sum(1 for r in results if not r["A"] and not r["AAAA"])
    return {
        "tries": len(results),
        "answered": both,
        "a_only": sum(1 for r in results if r["A"] and not r["AAAA"]),
        "aaaa_only": sum(1 for r in results if r["AAAA"] and not r["A"]),
        "unanswered": none,
    }


def run_pairs(server, name, tries=10, timeout=2.0, gap=0.2, port=53):
    """Send `tries` A+AAAA pairs, each pair on a fresh socket (one 5-tuple
    per pair, as glibc does per lookup). Returns summary + per-try results."""
    family = socket.AF_INET6 if ":" in server else socket.AF_INET
    results = []
    for i in range(tries):
        ids = random.sample(range(1, 0xFFFF), 2)
        queries = {
            ids[0]: ("A", build_query(ids[0], name, "A")),
            ids[1]: ("AAAA", build_query(ids[1], name, "AAAA")),
        }
        got = {"A": None, "AAAA": None}
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.connect((server, port))
            start = time.monotonic()
            for _qtype, packet in queries.values():
                sock.send(packet)
            sport = sock.getsockname()[1]
            deadline = start + timeout
            while None in got.values():
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                sock.settimeout(left)
                try:
                    data = sock.recv(4096)
                except (TimeoutError, ConnectionRefusedError):
                    break
                try:
                    reply = parse_response(data)
                except ValueError:
                    continue
                if reply["qr"] and reply["id"] in queries:
                    qtype = queries[reply["id"]][0]
                    got[qtype] = {
                        "rcode": reply["rcode"],
                        "answers": reply["answers"],
                        "ms": round((time.monotonic() - start) * 1000, 1),
                    }
        results.append({"try": i, "sport": sport, "A": got["A"], "AAAA": got["AAAA"]})
        if gap and i + 1 < tries:
            time.sleep(gap)
    return {"server": server, "name": name, **summarize(results), "results": results}
