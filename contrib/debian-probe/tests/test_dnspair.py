# SPDX-License-Identifier: LGPL-2.1-only
import struct

import pytest
from dprobe import dnspair


def test_build_query():
    q = dnspair.build_query(0x1234, "openwrt.lan", "AAAA")
    assert q[:12] == struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
    assert q[12:] == b"\x07openwrt\x03lan\x00" + struct.pack("!HH", 28, 1)


def test_encode_name_bad():
    with pytest.raises(ValueError):
        dnspair.encode_name("a..b")
    with pytest.raises(ValueError):
        dnspair.encode_name("x" * 64 + ".lan")


def response(qid, qtype, answers, rcode=0):
    """A response with compressed answer names pointing at the question."""
    question = dnspair.build_query(qid, "openwrt.lan", qtype)[12:]
    header = struct.pack("!HHHHHH", qid, 0x8180 | rcode, 1, len(answers), 0, 0)
    body = b""
    for rtype, rdata in answers:
        body += b"\xc0\x0c" + struct.pack("!HHIH", rtype, 1, 60, len(rdata)) + rdata
    return header + question + body


def test_parse_response():
    data = response(7, "A", [(1, bytes([192, 168, 1, 1])), (5, b"\xc0\x0c")])
    got = dnspair.parse_response(data)
    assert got == {
        "id": 7,
        "qr": True,
        "rcode": "NOERROR",
        "qname": "openwrt.lan",
        "qtype": "A",
        "answers": ["192.168.1.1"],
    }


def test_parse_response_aaaa_nxdomain():
    aaaa = bytes.fromhex("fd000000000000000000000000000001")
    assert dnspair.parse_response(response(8, "AAAA", [(28, aaaa)]))["answers"] == [
        "fd00::1"
    ]
    assert dnspair.parse_response(response(9, "A", [], rcode=3))["rcode"] == "NXDOMAIN"


@pytest.mark.parametrize(
    "data",
    [
        b"\x00" * 5,
        struct.pack("!HHHHHH", 1, 0x8180, 1, 0, 0, 0) + b"\x07openwrt",
        struct.pack("!HHHHHH", 1, 0x8180, 1, 0, 0, 0) + b"\xc0\x0c",  # loop
    ],
)
def test_parse_response_bad(data):
    with pytest.raises(ValueError):
        dnspair.parse_response(data)


def test_summarize():
    ok = {"rcode": "NOERROR", "answers": [], "ms": 1.0}
    results = [
        {"A": ok, "AAAA": ok},
        {"A": ok, "AAAA": None},
        {"A": None, "AAAA": ok},
        {"A": None, "AAAA": None},
    ]
    assert dnspair.summarize(results) == {
        "tries": 4,
        "answered": 1,
        "a_only": 1,
        "aaaa_only": 1,
        "unanswered": 1,
    }
