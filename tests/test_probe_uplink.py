"""
The DUT's internet uplink through its WAN port, per address family.

The lab's labgrid-probe (contrib/labgrid-probe) is the DUT's upstream: its
WAN namespace serves the DUT's WAN lease (router: the probe, DNS 1.1.1.1 and
9.9.9.9) and NATs to the internet (`online`, probe_fixtures.py). These tests
check the DUT's side of that path: WAN address and default route, ping to
the internet, DNS through the DUT's own resolver, HTTPS.

IPv6 skips until the DUT's wan6 has a global address and a default route:
the probe's WAN side is IPv4 only so far.

Skipped on QEMU, in labs without a probe and on boards without a cabled WAN
port (feature wan_port).
"""

# ruff: noqa: F811 (tests take the imported probe fixtures as arguments)

import json
import re

import pytest
from probe_fixtures import (
    interface_status,
    online,  # noqa: F401 (fixture)
    slot,  # noqa: F401 (fixture)
    wan_slot,  # noqa: F401 (fixture)
)

pytestmark = pytest.mark.lg_feature("wan_port")

HOST = "downloads.openwrt.org"
URL = f"https://{HOST}/.versions.json"
PING_V4 = ("1.1.1.1", "9.9.9.9")
PING_V6 = ("2606:4700:4700::1111", "2620:fe::fe")


def default_route(status, family):
    """The interface's default route from its netifd status, or None."""
    target = "0.0.0.0" if family == 4 else "::"
    for route in status.get("route", []):
        if route.get("target") == target and route.get("mask") == 0:
            return route
    return None


def ping(shell, host, record_property, key):
    """Ping host from the DUT; record the average RTT. Returns the output."""
    out, _, rc = shell.run(f"ping -c 3 -W 2 {host}", timeout=30)
    text = "\n".join(out)
    rtt = re.search(r"min/avg/max = [\d.]+/([\d.]+)/", text)
    record_property(key, rtt.group(1) if rtt else None)
    assert rc == 0, f"DUT can't ping {host}:\n{text}"
    return text


def resolve(shell, rtype, record_property, key):
    """Resolve HOST through the DUT's own resolver (its dnsmasq), return the
    addresses."""
    out, _, rc = shell.run(f"nslookup -type={rtype} {HOST} 127.0.0.1", timeout=30)
    text = "\n".join(out)
    pattern = (
        r"Address: (\d+\.\d+\.\d+\.\d+)"
        if rtype == "A"
        else r"Address: ([0-9a-f:]+:[0-9a-f:]*)"
    )
    addresses = sorted(set(re.findall(pattern, text)))
    record_property(key, json.dumps(addresses))
    assert rc == 0 and addresses, f"DUT resolves no {rtype} record for {HOST}:\n{text}"
    return addresses


def fetch(shell, family):
    """HTTPS (certificate checked) over one address family only."""
    out, err, rc = shell.run(f"uclient-fetch -{family} -q -O - {URL}", timeout=60)
    text = "\n".join(out)
    assert rc == 0 and "stable_version" in text, (
        f"uclient-fetch -{family} {URL} failed (rc {rc}):\n" + "\n".join(out + err)
    )


def test_uplink_ipv4(online, ssh_command, record_property):
    """The DUT reaches the internet over IPv4 through its WAN: WAN address
    and default route from the probe's lease, ping to public resolvers, A
    records through its dnsmasq, HTTPS to downloads.openwrt.org."""
    status = interface_status(ssh_command, "wan") or {}
    addrs = status.get("ipv4-address", [])
    route = default_route(status, 4)
    record_property(
        "wan_ipv4", json.dumps([f"{a['address']}/{a['mask']}" for a in addrs])
    )
    record_property("wan_ipv4_gateway", route.get("nexthop") if route else None)
    assert addrs, f"wan has no IPv4 address: {status}"
    assert route, f"wan has no IPv4 default route: {status.get('route')}"

    for host in PING_V4:
        ping(ssh_command, host, record_property, f"ping_{host}_avg_ms")
    resolve(ssh_command, "A", record_property, "dns_a")
    fetch(ssh_command, 4)


def test_uplink_ipv6(online, ssh_command, record_property):
    """The DUT reaches the internet over IPv6 through its WAN: global wan6
    address and default route, ping to public resolvers, AAAA records through
    its dnsmasq, HTTPS to downloads.openwrt.org."""
    status = interface_status(ssh_command, "wan6") or {}
    addrs = [
        a
        for a in status.get("ipv6-address", [])
        + status.get("ipv6-prefix-assignment", [])
        if not a.get("address", "").lower().startswith("fe80")
    ]
    route = default_route(status, 6)
    record_property(
        "wan6_ipv6", json.dumps([f"{a['address']}/{a['mask']}" for a in addrs])
    )
    if not addrs or not route:
        pytest.skip(
            "wan6 has no global address or default route: the probe's WAN side offers no IPv6"
        )

    for host in PING_V6:
        ping(ssh_command, host, record_property, f"ping_{host}_avg_ms")
    resolve(ssh_command, "AAAA", record_property, "dns_aaaa")
    fetch(ssh_command, 6)
