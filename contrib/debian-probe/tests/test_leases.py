# SPDX-License-Identifier: LGPL-2.1-only
from dprobe import leases


def test_dnsmasq():
    text = (
        "1790000000 02:44:50:01:01:2e 172.16.100.42 OpenWrt 01:02:44:50:01:01:2e\n"
        "1790000100 AA:BB:CC:DD:EE:FF 172.16.100.43 * *\n"
        "duid 00:01:00:01:aa:bb\n"
        "garbage\n"
    )
    got = leases.dnsmasq(text)
    assert got == [
        {
            "expires": 1790000000,
            "mac": "02:44:50:01:01:2e",
            "ip": "172.16.100.42",
            "hostname": "OpenWrt",
            "client_id": "01:02:44:50:01:01:2e",
        },
        {
            "expires": 1790000100,
            "mac": "aa:bb:cc:dd:ee:ff",
            "ip": "172.16.100.43",
            "hostname": None,
            "client_id": None,
        },
    ]


KEA_HEADER = (
    "address,duid,valid_lifetime,expire,subnet_id,pref_lifetime,lease_type,iaid,"
    "prefix_len,fqdn_fwd,fqdn_rev,hostname,hwaddr,state,user_context,hwtype,hwaddr_source\n"
)


def test_kea6_latest_row_wins():
    text = KEA_HEADER + (
        "2001:db8:100::1000,00:03:00:01:aa,3600,1790003600,1,1800,0,1,128,0,0,,,0,,1,0\n"
        "2001:db8:200::,00:03:00:01:aa,3600,1790003600,1,1800,2,2,60,0,0,,,0,,1,0\n"
        "2001:db8:200::,00:03:00:01:aa,7200,1790007200,1,3600,2,2,60,0,0,,,0,,1,0\n"
        "2001:db8:100::1001,00:03:00:01:bb,3600,1790003600,1,1800,0,1,128,0,0,,,0,,1,0\n"
        "2001:db8:100::1001,00:03:00:01:bb,0,1790000000,1,0,0,1,128,0,0,,,0,,1,0\n"
    )
    got = {(lease["type"], lease["address"]): lease for lease in leases.kea6(text)}
    assert set(got) == {("na", "2001:db8:100::1000"), ("pd", "2001:db8:200::")}
    pd = got[("pd", "2001:db8:200::")]
    assert (pd["prefix_len"], pd["valid"], pd["preferred"], pd["iaid"]) == (
        60,
        7200,
        3600,
        2,
    )


def test_kea6_empty():
    assert leases.kea6("") == []
    assert leases.kea6(KEA_HEADER) == []
