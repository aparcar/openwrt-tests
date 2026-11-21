"""
The DUT's WAN side against a simulated ISP: DHCPv4 options, the IPv6 route
metric, and a delegated IPv6 prefix that must survive reconfiguration.

Runs through the Debian lab probe (contrib/debian-probe).

The probe plays the DUT's ISP on its WAN VLAN: dnsmasq (DHCPv4 with custom
options), kea-dhcp6 (IA_NA + IA_PD), radvd and chrony, all in the slot's
WAN namespace with the addresses of dprobe's WanPlan (172.16.100.1,
2001:db8:100::1, PD pool 2001:db8:200::/48).

Skipped without a coordinator (QEMU), without a role=debian-probe place, or
if the DUT's place has no "wan" NetworkInterface.
"""

import ipaddress
import logging
import re
import time

import pytest
from debian_probe_fixtures import (
    PD_POOL,
    WAN_NET4,
    dut_config,
    interface_status,
    record_json,
    uci,
    wait_for_interface,
)

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.lg_feature("wan_port")


def ipv4_on(status, network):
    net = ipaddress.ip_network(network)
    return [
        a
        for a in status.get("ipv4-address", [])
        if ipaddress.ip_address(a["address"]) in net
    ]


def delegated(status):
    """The prefixes of wan6's status that come from the probe's PD pool."""
    pool = ipaddress.ip_network(PD_POOL)
    return [
        f"{p['address']}/{p['mask']}"
        for p in status.get("ipv6-prefix", [])
        if ipaddress.ip_network(f"{p['address']}/{p['mask']}", strict=False).subnet_of(
            pool
        )
    ]


def wan_dhcp4_lease(shell, slot, **options):
    """(Re)start the probe's DHCPv4 server and make the DUT ask again."""
    slot.check("wan", "dhcp4", **options)
    shell.run_check("ifup wan")
    return wait_for_interface(
        shell, "wan", timeout=90, check=lambda s: ipv4_on(s, WAN_NET4)
    )


def wan6_up(shell, timeout=120):
    shell.run_check("ifup wan6")
    return wait_for_interface(shell, "wan6", timeout=timeout, check=delegated)


def test_dprobe_wan_dhcp4(shell_command, dprobe_wan, record_property):
    """The DUT's WAN port gets an IPv4 address by DHCP from the upstream
    (ISP-like) server and can reach it. That's the most common way a router
    gets its internet connection."""
    status = wan_dhcp4_lease(shell_command, dprobe_wan)
    record_json(record_property, "dut_wan_ipv4", status["ipv4-address"])
    leases = dprobe_wan.check("wan", "leases")["dhcp4"]
    assert leases, "the probe's dnsmasq has no lease for the DUT"
    shell_command.run_check(f"ping -c 3 -W 2 {dprobe_wan.info['wan_ip']}")


def test_wan_dhcp_broadcast_option(shell_command, dprobe_wan, record_property):
    """The DUT uses the broadcast address its WAN DHCP server sends (option
    28) instead of deriving one from the netmask. Some providers' networks
    rely on that setting.

    Regression: openwrt/openwrt#24782

    dhcp.script must apply DHCP option 28 (broadcast address). The probe
    sends one that differs from what the /24 implies, so a script that
    derives it from the netmask shows up.
    """
    broadcast = "172.16.100.127"
    status = wan_dhcp4_lease(shell_command, dprobe_wan, option=[f"28={broadcast}"])
    dev = status["l3_device"]
    out = "\n".join(shell_command.run_check(f"ip -4 -o addr show dev {dev}"))
    record_property("dut_wan_addr", out)
    brd = re.findall(r"inet 172\.16\.100\.\d+/\d+ brd (\S+)", out)
    assert brd, f"no WAN address with a broadcast on {dev}:\n{out}"
    assert brd[0] == broadcast, f"broadcast {brd[0]}, option 28 said {broadcast}"


def test_wan6_metric_applied(shell_command, dprobe_wan, record_property):
    """The route metric configured on wan6 is applied to the IPv6 routes it
    learns. Users set it to prefer one uplink over another, and an ignored
    metric sends traffic out the wrong way.

    Regression: openwrt/openwrt#10188

    With `metric` set on wan6, the routes odhcp6c learns (default route via
    the RA router, routes for the delegated prefix) carry that metric.
    """
    metric = 512
    dprobe_wan.check("wan", "ra")
    dprobe_wan.check("wan", "dhcp6")
    with dut_config(shell_command, "network"):
        uci(shell_command, f"set network.wan6.metric={metric}")
        shell_command.run_check("/etc/init.d/network reload")
        status = wan6_up(shell_command)
        dev = status["l3_device"]
        # odhcp6c hands the routes to netifd shortly after the prefix
        time.sleep(5)
        routes = shell_command.run_check("ip -6 route show table all")
        record_json(record_property, "dut_ipv6_routes", routes)
        defaults = [
            r for r in routes if r.startswith("default") and f"dev {dev} " in f"{r} "
        ]
        assert defaults, f"no IPv6 default route on {dev}:\n" + "\n".join(routes)
        wrong = [r for r in defaults if f"metric {metric}" not in r]
        assert not wrong, f"default routes without metric {metric}: {wrong}"


@pytest.mark.timeout(600)  # waits up to 420 s for the delegated prefix
def test_wan6_pd_survives_reconfigure(shell_command, dprobe_wan, record_property):
    """After the upstream DHCPv6 server goes away until the delegated IPv6
    prefix expires and then comes back, the DUT gets a prefix again. If it
    doesn't, the LAN keeps no IPv6 after a provider outage until someone
    restarts the WAN.

    Regression: openwrt/openwrt#13454

    The PD-expiry half of the issue: the DHCPv6 server disappears until the
    delegated prefix has expired, then comes back with no bindings. wan6
    must get a prefix again. (kea can't send Reconfigure without a
    commercial hook, so that trigger isn't covered.)
    """
    lifetimes = {"preferred": 60, "valid": 120, "t1": 20, "t2": 40}
    dprobe_wan.check("wan", "ra")
    dprobe_wan.check("wan", "dhcp6", **lifetimes)
    first = delegated(wan6_up(shell_command))
    record_json(record_property, "pd_before", first)

    dprobe_wan.check("wan", "stop", "dhcp6")
    time.sleep(lifetimes["valid"] + 30)
    during = interface_status(shell_command, "wan6") or {}
    record_json(
        record_property,
        "wan6_during_outage",
        {"up": during.get("up"), "prefixes": delegated(during)},
    )

    dprobe_wan.check("wan", "dhcp6", **lifetimes)
    # odhcp6c's SOLICIT backoff can be several minutes by now
    status = wait_for_interface(shell_command, "wan6", timeout=420, check=delegated)
    record_json(record_property, "pd_after", delegated(status))
    logger.info("PD before %s, after %s", first, delegated(status))
