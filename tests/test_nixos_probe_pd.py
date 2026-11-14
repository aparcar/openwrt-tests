"""
DHCPv6 prefix delegation: the DUT takes an address and a prefix from its
upstream's DHCPv6 server on wan6, hands a /64 of the prefix to its LAN, and
routes the LAN's IPv6 traffic out through the WAN.

Runs against the NixOS probe (contrib/nixos-probe): kea-dhcp6 and radvd in
its WAN namespace are the DUT's upstream (2001:db8:<wan id>::/48, prefixes
from 2001:db8:<wan id>:100::/56), a LAN namespace is a client behind the
DUT. The OpenWrt probe can't serve prefixes (no DHCPv6-PD server in
dnsmasq).
"""

# ruff: noqa: F811 (tests take the imported probe fixtures as arguments)

import ipaddress
import json
import time

import pytest
from nixos_probe_fixtures import (  # noqa: F401 (fixtures)
    nixos_probe,
    nixos_slot,
    nixos_wan_slot,
    wait_for_wan6_prefix,
    wan6_prefix,
)

pytestmark = pytest.mark.lg_feature("wan_port")


def ubus_status(shell, iface):
    return json.loads(
        "\n".join(shell.run_check(f"ubus call network.interface.{iface} status"))
    )


def lan_prefix(shell, delegated):
    """The /64 (or longer) the DUT assigned its LAN from `delegated`."""
    status = ubus_status(shell, "lan")
    for a in status.get("ipv6-prefix-assignment", []):
        net = ipaddress.ip_network(f"{a['address']}/{a['mask']}")
        if net.subnet_of(delegated):
            return net
    pytest.fail(
        f"LAN has no prefix from {delegated}: {status.get('ipv6-prefix-assignment')}"
    )


def wait_slaac(slot, network, timeout=60):
    """The LAN client's SLAAC address in `network`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        out = slot.lan_check("ip -j -6 addr show dev eth0 scope global")
        for iface in json.loads("\n".join(out) or "[]"):
            for a in iface.get("addr_info", []):
                address = ipaddress.ip_address(a["local"])
                if address in network and "tentative" not in a:
                    return address
        time.sleep(2)
    pytest.fail(f"LAN client got no address in {network} within {timeout}s")


def test_wan6_address_and_prefix(nixos_wan_slot, shell_command):
    """wan6 gets an address (IA_NA) from the upstream's link and a delegated
    prefix (IA_PD) of the length the server hands out, and the server has
    both leased to the DUT."""
    slot = nixos_wan_slot
    plan = slot.info["ipv6"]
    status = ubus_status(shell_command, "wan6")
    link = ipaddress.ip_network(plan["link"])
    addresses = [
        ipaddress.ip_address(a["address"]) for a in status.get("ipv6-address", [])
    ]
    assert any(a in link for a in addresses), (
        f"wan6 has no address from {link}: {addresses}"
    )
    assert slot.prefix.prefixlen == plan["delegated_len"], slot.prefix

    leases = slot.leases()
    assert any(
        lease["type"] == "pd" and lease["address"] == str(slot.prefix)
        for lease in leases
    ), f"no PD lease for {slot.prefix} on the probe: {leases}"
    na = [
        ipaddress.ip_address(lease["address"])
        for lease in leases
        if lease["type"] == "na"
    ]
    assert set(na) & set(addresses), f"probe's NA leases {na}, wan6 has {addresses}"


def test_lan_prefix_from_delegation(nixos_wan_slot, shell_command):
    """odhcpd assigns the LAN a /64 out of the delegated prefix
    (ip6assign 60 by default)."""
    net = lan_prefix(shell_command, nixos_wan_slot.prefix)
    assert net.prefixlen == 64, net


def test_lan_ra_carries_delegated_prefix(nixos_wan_slot, shell_command):
    """A Router Solicitation on the LAN (sent with scapy) is answered with an
    RA that announces the LAN's /64 from the delegated prefix, on-link and
    autonomous."""
    net = lan_prefix(shell_command, nixos_wan_slot.prefix)
    out = nixos_wan_slot.scapy(
        "lan",
        """
import json
rs = Ether(dst="33:33:00:00:00:02") / IPv6(dst="ff02::2") / ICMPv6ND_RS()
ra = srp1(rs, iface="eth0", timeout=10, filter="icmp6 and ip6[40] == 134")
prefixes = []
opt = ra and ra.getlayer(ICMPv6NDOptPrefixInfo)
while opt:
    prefixes.append({"prefix": f"{opt.prefix}/{opt.prefixlen}", "L": opt.L, "A": opt.A})
    opt = opt.payload.getlayer(ICMPv6NDOptPrefixInfo)
print(json.dumps(prefixes if ra else None))
""",
    )
    prefixes = json.loads(out[-1])
    assert prefixes is not None, "no RA within 10 s after a Router Solicitation"
    announced = {p["prefix"]: p for p in prefixes}
    assert str(net) in announced, f"RA announces {list(announced)}, not {net}"
    assert announced[str(net)]["L"] and announced[str(net)]["A"], announced[str(net)]


def test_lan_client_routed_through_wan(nixos_wan_slot, shell_command):
    """A LAN client with a SLAAC address from the delegated prefix reaches
    the upstream router through the DUT, and the reply comes back (the
    upstream routes the prefix to the DUT's wan6 address, as an ISP does)."""
    slot = nixos_wan_slot
    net = lan_prefix(shell_command, slot.prefix)
    client = wait_slaac(slot, net)
    na = next(lease["address"] for lease in slot.leases() if lease["type"] == "na")
    slot.wan_check(f"ip -6 route replace {slot.prefix} via {na} dev eth0")
    router = slot.info["ipv6"]["router"]
    out, _, rc = slot.lan(f"ping -6 -c 3 -W 2 {router}")
    if rc != 0:
        routes, _, _ = shell_command.run("ip -6 route; ip -6 rule")
        pytest.fail(
            f"LAN client {client} can't reach {router} through the DUT:\n"
            + "\n".join(out + ["# DUT:"] + routes)
        )


def test_wan6_release_returns_prefix(nixos_wan_slot, shell_command):
    """`ifdown wan6` releases the address and prefix at the server (DHCPv6
    RELEASE), and `ifup wan6` gets a prefix again."""
    slot = nixos_wan_slot
    shell_command.run_check("ifdown wan6")
    deadline = time.monotonic() + 15
    while slot.leases() and time.monotonic() < deadline:
        time.sleep(1)
    left = slot.leases()
    shell_command.run_check("ifup wan6")
    assert not left, f"leases still active after ifdown wan6: {left}"
    slot.prefix = wait_for_wan6_prefix(shell_command, slot.pd_pool)
    assert wan6_prefix(shell_command, slot.pd_pool) == slot.prefix
