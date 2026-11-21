# SPDX-License-Identifier: LGPL-2.1-only
"""Decoders of the scapy-based DHCP clients (skipped where scapy is missing;
on the probe: python3-scapy)."""

import pytest

pytest.importorskip("scapy")

from dprobe import dhcp4, dhcp6
from scapy.layers import dhcp6 as d6
from scapy.layers.dhcp import BOOTP, DHCP
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import Ether
from scapy.utils import mac2str


def test_dhcp4_decode_offer():
    pkt = Ether(
        bytes(
            Ether()
            / IP()
            / UDP(sport=67, dport=68)
            / BOOTP(
                op=2,
                xid=42,
                yiaddr="172.16.100.42",
                chaddr=mac2str("02:44:50:01:01:2e"),
            )
            / DHCP(
                options=[
                    ("message-type", "offer"),
                    ("subnet_mask", "255.255.255.0"),
                    ("router", "172.16.100.1"),
                    ("name_server", "172.16.100.1", "9.9.9.9"),
                    ("broadcast_address", "172.16.100.127"),
                    ("lease_time", 43200),
                    ("server_id", "172.16.100.1"),
                    "end",
                ]
            )
        )
    )
    reply = dhcp4.decode(pkt)
    assert reply["message_type"] == "OFFER"
    assert reply["chaddr"] == "02:44:50:01:01:2e"
    lease = dhcp4.lease_from(reply)
    assert lease["ip"] == "172.16.100.42"
    assert lease["prefixlen"] == 24
    assert lease["broadcast"] == "172.16.100.127"
    assert lease["router"] == ["172.16.100.1"]
    assert lease["dns"] == ["172.16.100.1", "9.9.9.9"]
    assert lease["lease_time"] == 43200


def test_dhcp6_build_and_decode():
    advertise = bytes(
        d6.DHCP6_Advertise(trid=0x123456)
        / d6.DHCP6OptClientId(duid=d6.DUID_LL(lladdr="02:44:50:00:00:66"))
        / d6.DHCP6OptServerId(duid=d6.DUID_LL(lladdr="02:44:50:01:01:2e"))
        / d6.DHCP6OptIA_NA(
            iaid=1,
            T1=30,
            T2=48,
            ianaopts=[
                d6.DHCP6OptIAAddress(
                    addr="2001:db8:100::1000", preflft=60, validlft=120
                )
            ],
        )
        / d6.DHCP6OptIA_PD(
            iaid=2,
            T1=30,
            T2=48,
            iapdopt=[
                d6.DHCP6OptIAPrefix(
                    prefix="2001:db8:200::", plen=60, preflft=60, validlft=120
                )
            ],
        )
        / d6.DHCP6OptDNSServers(dnsservers=["2001:db8:100::1"])
    )
    got = dhcp6.decode(advertise)
    assert got["message_type"] == "ADVERTISE"
    assert got["trid"] == 0x123456
    assert got["ia_na"][0]["addresses"][0]["address"] == "2001:db8:100::1000"
    assert got["ia_pd"][0]["prefixes"][0] == {
        "prefix": "2001:db8:200::/60",
        "preferred": 60,
        "valid": 120,
        "status": None,
    }
    assert got["dns"] == ["2001:db8:100::1"]

    request = dhcp6.decode(
        dhcp6.build(
            "request", 7, "02:44:50:00:00:66", server_duid=got["server_duid"], ias=got
        )
    )
    assert request["message_type"] == "REQUEST"
    assert request["server_duid"] == got["server_duid"]
    assert request["ia_pd"][0]["prefixes"][0]["prefix"] == "2001:db8:200::/60"
