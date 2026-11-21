"""
The DUT's LAN services as an ordinary Linux client sees them: DHCPv4 leases,
IPv6 Router Advertisements and DNS answers, including corner cases that
broke real clients before.

Runs through the Debian lab probe (contrib/debian-probe).

The probe's slot namespace on the DUT's LAN VLAN is a plain LAN client with
tools OpenWrt doesn't have: a decoding RA capture, scapy DHCP clients and a
DNS sender that reproduces glibc's back-to-back A+AAAA queries.

Skipped without a coordinator (QEMU) or without a role=debian-probe place.
"""

import ipaddress
import time

import pytest
from debian_probe_fixtures import (
    DUT_LAN_IP,
    dut_config,
    record_json,
    uci,
    wait_for_interface,
)

DUT_LAN = ipaddress.ip_network("192.168.1.0/24")


def capture_ras(slot, duration=20):
    """RAs on the LAN (after a Router Solicitation), only well-formed ones."""
    result = slot.call("ra-capture", duration=duration)
    return [
        r for r in result.get("ras", []) if "error" not in r and r["valid_hop_limit"]
    ]


def test_dprobe_lan_dhcp4(dprobe_lan_lease, record_property):
    """The DUT's DHCPv4 server gives a LAN client an address in the LAN
    subnet, with the DUT as router. Without it, devices on the LAN get no
    network configuration."""
    record_json(record_property, "dprobe_lan_lease", dprobe_lan_lease)
    assert ipaddress.ip_address(dprobe_lan_lease["ip"]) in DUT_LAN
    assert dprobe_lan_lease["router"][:1] == [DUT_LAN_IP], dprobe_lan_lease


def test_dprobe_lan_ra(dprobe_slot, record_property):
    """The DUT answers a LAN client's IPv6 Router Solicitation with a
    well-formed Router Advertisement that has a prefix. LAN clients need it
    for automatic IPv6 configuration."""
    ras = capture_ras(dprobe_slot, duration=15)
    record_json(record_property, "lan_ras", ras[:3])
    assert ras, "no router advertisement on the LAN within 15s"
    assert ras[-1]["source"].startswith("fe80:"), ras[-1]
    assert ras[-1]["prefixes"], f"RA without a prefix: {ras[-1]}"


@pytest.mark.lg_feature("wan_port")
def test_odhcpd_ra_with_device_default_route(
    shell_command, dprobe_wan, record_property
):
    """The DUT keeps announcing itself as the IPv6 default router on the LAN
    when its IPv6 default route has no gateway, as with point-to-point
    uplinks. If it doesn't, LAN clients lose IPv6 internet access.

    Regression: openwrt/openwrt#23246

    A default route without a nexthop (static `route6 ::/0` on wan6, as
    with PPP-like or point-to-point uplinks) is still a default route:
    odhcpd must keep sending RAs with a non-zero router lifetime.
    """
    shell = shell_command
    with dut_config(shell, "network"):
        uci(
            shell,
            "set network.wan6.proto=static",
            "set network.wan6.ip6addr=2001:db8:100::2/64",
            "set network.wan6.ip6prefix=2001:db8:300::/48",
            "add network route6",
            "set network.@route6[-1].interface=wan6",
            "set network.@route6[-1].target=::/0",
        )
        shell.run_check("/etc/init.d/network reload")
        wait_for_interface(shell, "wan6", timeout=60)
        routes = shell.run_check("ip -6 route show default")
        record_json(record_property, "dut_default_routes", routes)
        assert any("via" not in r for r in routes), (
            f"precondition: no nexthop-less default route: {routes}"
        )
        # the issue: RAs stop / lifetime 0 after odhcpd restarts
        shell.run_check("/etc/init.d/odhcpd restart")
        time.sleep(3)
        ras = capture_ras(dprobe_wan, duration=30)
        record_json(record_property, "lan_ras", ras[-3:])
        assert ras, "odhcpd sent no RA within 30s after restart"
        lifetimes = [r["router_lifetime"] for r in ras]
        assert lifetimes[-1] > 0, f"router lifetimes {lifetimes}: not a default router"


def test_lan_dns_udp_pair(
    shell_command, dprobe_slot, dprobe_lan_lease, record_property
):
    """The DUT's DNS answers A and AAAA queries that arrive back to back, as
    Linux clients (glibc) send them. When it doesn't, name lookups from such
    clients hang or time out.

    Regression: openwrt/openwrt#16261

    A and AAAA queries sent back to back on one socket (what glibc does) all
    get answers. With rx-gro-list on the LAN driver (bcmgenet) the two
    datagrams are merged and dnsmasq never answers.
    """
    hostname = shell_command.run_check("uci -q get system.@system[0].hostname")[0]
    name = f"{hostname}.lan"
    result = dprobe_slot.call("dns-pair", DUT_LAN_IP, name, tries=20, gap=0.2)
    summary = {
        k: result.get(k)
        for k in ("tries", "answered", "a_only", "aaaa_only", "unanswered")
    }
    record_json(record_property, "dns_pair", summary)
    assert result.ok, f"{name} via {DUT_LAN_IP}: {summary}"
