"""
The IPv6 Router Advertisements the DUT sends on its LAN, which LAN clients
use to configure IPv6 (prefixes, default route, DNS): they carry what
odhcpd's configuration says.

Captured and parsed by the labgrid-probe (contrib/labgrid-probe, method
ra_capture).

The probe sends a Router Solicitation from its LAN client namespace and
returns the RAs it receives (router lifetime, M/O flags, prefixes with
lifetimes, routes, RDNSS, DNSSL, MTU). Tests change odhcpd's config on the
DUT and restore it afterwards.
"""

# ruff: noqa: F811 (tests take the imported probe fixtures as arguments)

import json
import time

import pytest
from probe_fixtures import lease, slot  # noqa: F401 (fixtures)


def solicit_ra(slot, timeout=5):
    """One RA from the DUT, solicited."""
    ras = slot.ra_capture(timeout=timeout, solicit=True, count=1)["ras"]
    assert ras, f"no RA from the DUT within {timeout}s after a Router Solicitation"
    return ras[0]


class UciChanges:
    """Set UCI options on the DUT, commit, and restore the old values."""

    def __init__(self, shell):
        self.shell = shell
        self.saved = {}

    def set(self, option, value):
        if option not in self.saved:
            out, _, rc = self.shell.run(f"uci -q get {option}")
            self.saved[option] = out[0] if rc == 0 and out else None
        self.shell.run_check(f"uci set {option}='{value}'")
        self.shell.run_check(f"uci commit {option.split('.')[0]}")

    def restore(self):
        for option, value in self.saved.items():
            if value is None:
                self.shell.run(f"uci -q delete {option}")
            else:
                self.shell.run(f"uci set {option}='{value}'")
            self.shell.run(f"uci commit {option.split('.')[0]}")
        self.saved = {}


def restart_odhcpd(shell):
    shell.run_check("/etc/init.d/odhcpd restart")
    time.sleep(3)


@pytest.fixture
def uci(shell_command):
    changes = UciChanges(shell_command)
    yield changes
    if changes.saved:
        changes.restore()
        restart_odhcpd(shell_command)


def test_lan_ra(slot, lease, record_property):
    """The DUT answers a LAN client's IPv6 Router Solicitation with a Router
    Advertisement that carries a /64 prefix for automatic addressing (SLAAC),
    with sane lifetimes. LAN clients use it to configure IPv6."""
    ra = solicit_ra(slot)
    record_property("ra", json.dumps(ra))

    assert ra["hop_limit"] > 0, ra
    slaac = [p for p in ra["prefixes"] if p["on_link"] and p["autonomous"]]
    assert slaac, f"no on-link autonomous prefix in the RA: {ra['prefixes']}"
    for p in ra["prefixes"]:
        assert p["length"] == 64 or not p["autonomous"], p
        assert p["preferred_lifetime"] <= p["valid_lifetime"], p


@pytest.mark.xfail(
    reason="openwrt/openwrt#10804: odhcpd ignores ra_useleasetime "
    "(valid lifetime 5400 s with leasetime 30m, 25.12.6)"
)
def test_ra_prefix_lifetimes(slot, lease, uci, shell_command, record_property):
    """With `ra_useleasetime` set, the IPv6 prefix lifetimes the DUT
    advertises are capped at the DHCP lease time. Otherwise clients keep
    addresses longer than configured. This is a known bug, expected to fail.

    Regression: openwrt/openwrt#10804

    With ra_useleasetime, the prefix lifetimes in the RAs are limited to the
    DHCP lease time, and preferred never exceeds valid.
    """
    leasetime = 1800
    uci.set("dhcp.lan.ra_useleasetime", "1")
    uci.set("dhcp.lan.leasetime", f"{leasetime // 60}m")
    restart_odhcpd(shell_command)

    ra = solicit_ra(slot)
    record_property("ra_prefixes", json.dumps(ra["prefixes"]))
    assert ra["prefixes"], ra
    for p in ra["prefixes"]:
        assert p["preferred_lifetime"] <= p["valid_lifetime"], p
        assert p["valid_lifetime"] <= leasetime, (
            f"{p['prefix']}/{p['length']}: valid lifetime {p['valid_lifetime']} "
            f"> leasetime {leasetime}"
        )
        assert p["preferred_lifetime"] <= leasetime, p


@pytest.fixture
def device_default_route(shell_command):
    """An IPv6 default route without a nexthop (device route), as wireguard
    or a dummy interface would install it."""
    route = "default dev lo metric 4096"
    _, _, rc = shell_command.run(f"ip -6 route add {route}")
    if rc != 0:
        pytest.skip("can't add an IPv6 device route on the DUT (ip -6)")
    yield
    shell_command.run(f"ip -6 route del {route}")


def test_odhcpd_ra_with_device_default_route(
    slot, lease, uci, device_default_route, shell_command, record_property
):
    """The DUT keeps announcing itself as the IPv6 default router when its
    default route points at a device without a gateway, as with WireGuard or
    other tunnels. If it doesn't, LAN clients lose their IPv6 route.

    Regression: openwrt/openwrt#23246

    odhcpd treats a default route without a nexthop as a default route: the
    RAs keep a router lifetime > 0 (ra_default=1: no public prefix needed),
    also in later RAs, and odhcpd logs no "No default route present".
    """
    uci.set("dhcp.lan.ra_default", "1")
    marker = f"labgrid-probe-{time.time_ns()}"
    shell_command.run_check(f"logger -t labgrid-probe {marker}")
    restart_odhcpd(shell_command)

    lifetimes = []
    for _ in range(3):
        lifetimes.append(solicit_ra(slot)["router_lifetime"])
        time.sleep(5)
    record_property("ra_router_lifetimes", json.dumps(lifetimes))

    log = shell_command.run("logread")[0]
    since = next((i for i, line in enumerate(log) if marker in line), 0)
    complaints = [line for line in log[since:] if "No default route" in line]
    assert not complaints, "\n".join(complaints)
    assert all(t > 0 for t in lifetimes), (
        f"router lifetimes {lifetimes} with a device default route"
    )
