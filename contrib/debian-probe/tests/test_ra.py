# SPDX-License-Identifier: LGPL-2.1-only
import ipaddress
import struct

import pytest
from dprobe import ra


def ra_bytes(flags=0xC0, lifetime=1800, options=b""):
    return struct.pack("!BBHBBHII", 134, 0, 0, 64, flags, lifetime, 0, 0) + options


def opt_prefix(prefix, plen, flags=0xC0, valid=7200, preferred=3600):
    return (
        struct.pack("!BBBBIII", 3, 4, plen, flags, valid, preferred, 0)
        + ipaddress.IPv6Address(prefix).packed
    )


def opt_rdnss(lifetime, *servers):
    return struct.pack("!BBHI", 25, 1 + 2 * len(servers), 0, lifetime) + b"".join(
        ipaddress.IPv6Address(s).packed for s in servers
    )


def opt_dnssl(lifetime, *names):
    raw = b"".join(
        b"".join(bytes([len(label)]) + label.encode() for label in n.split(".")) + b"\0"
        for n in names
    )
    raw += bytes(-(len(raw) + 8) % 8)
    return struct.pack("!BBHI", 31, (len(raw) + 8) // 8, 0, lifetime) + raw


def test_decode_full():
    body = ra_bytes(
        flags=0x80 | 0x08,  # M, preference high
        options=(
            bytes([1, 1])
            + bytes.fromhex("024450010066")
            + struct.pack("!BBHI", 5, 1, 0, 1492)
            + opt_prefix("fd00:1::", 64)
            + struct.pack("!BBBBI", 24, 2, 48, 0x18, 600)
            + ipaddress.IPv6Address("2001:db8:300::").packed[:8]
            + opt_rdnss(900, "fd00:1::1", "fd00:1::2")
            + opt_dnssl(900, "lan", "example.org")
            + bytes([99, 1, 0, 0, 0, 0, 0, 0])
        ),
    )
    got = ra.decode_ra(body)
    assert got["managed"] and not got["other"]
    assert got["preference"] == "high"
    assert got["router_lifetime"] == 1800
    assert got["cur_hop_limit"] == 64
    assert got["source_lla"] == "02:44:50:01:00:66"
    assert got["mtu"] == 1492
    assert got["prefixes"] == [
        {
            "prefix": "fd00:1::/64",
            "on_link": True,
            "autonomous": True,
            "router_address": False,
            "valid": 7200,
            "preferred": 3600,
        }
    ]
    assert got["routes"] == [
        {"prefix": "2001:db8:300::/48", "preference": "low", "lifetime": 600}
    ]
    assert [r["address"] for r in got["rdnss"]] == ["fd00:1::1", "fd00:1::2"]
    assert [d["domain"] for d in got["dnssl"]] == ["lan", "example.org"]
    assert got["other_options"] == [99]


def test_decode_lifetime_zero():
    got = ra.decode_ra(ra_bytes(flags=0, lifetime=0))
    assert got["router_lifetime"] == 0
    assert not got["managed"]
    assert got["preference"] == "medium"


@pytest.mark.parametrize(
    "body",
    [
        b"",
        bytes([133]) + bytes(15),  # RS, not RA
        ra_bytes()[:10],
        ra_bytes(options=bytes([3, 0])),  # length 0
        ra_bytes(options=bytes([3, 4]) + bytes(10)),  # runs past the end
    ],
)
def test_decode_bad(body):
    with pytest.raises(ValueError):
        ra.decode_ra(body)


def test_build_rs():
    assert ra.build_rs() == bytes([133, 0, 0, 0, 0, 0, 0, 0])
    assert ra.build_rs("02:44:50:00:00:66")[8:] == bytes(
        [1, 1, 2, 0x44, 0x50, 0, 0, 0x66]
    )


def test_icmp6_filter_passes_only_ra():
    words = struct.unpack("8I", ra._icmp6_filter(134))
    assert words[134 >> 5] == 0xFFFFFFFF & ~(1 << (134 & 31))
    assert all(w == 0xFFFFFFFF for i, w in enumerate(words) if i != 134 >> 5)
