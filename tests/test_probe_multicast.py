"""
IPv4 multicast (e.g. IPTV) from the internet side reaches clients on the LAN
when igmpproxy runs on the DUT, and stops once they leave the group.

Uses the labgrid-probe (contrib/labgrid-probe):
a sender in the probe's WAN namespace, a joiner (IGMP) in its LAN namespace,
and igmpproxy on the DUT forwarding the group from WAN to LAN.

igmpproxy is installed over the DUT's WAN (online fixture) if the image
lacks it. The DUT keeps its shipped
/etc/config/igmpproxy (upstream wan, downstream lan); the tests only add
firewall rules for IGMP and the forwarded group, and remove them again.
"""

# ruff: noqa: F811 (tests take the imported probe fixtures as arguments)

import json
import time

import pytest
from probe_fixtures import (
    lease,  # noqa: F401 (fixture)
    online,  # noqa: F401 (fixture)
    slot,  # noqa: F401 (fixture)
    wait_for_interface,
    wan_slot,  # noqa: F401 (fixture)
)

pytestmark = pytest.mark.lg_feature("wan_port")

GROUP = "239.255.42.1"
PORT = 5001
PACKETS = 50
INTERVAL_MS = 100

# fw4 rules for igmpproxy: IGMP from WAN, the group forwarded WAN -> LAN
FW_RULES = {
    "lgp_igmp": {"src": "wan", "proto": "igmp", "target": "ACCEPT"},
    "lgp_mcast": {
        "src": "wan",
        "dest": "lan",
        "proto": "udp",
        "dest_ip": "224.0.0.0/4",
        "target": "ACCEPT",
    },
}


@pytest.fixture(scope="module")
def igmpproxy(wan_slot, lease, online, strategy):
    shell = strategy.shell
    if shell.run("apk info -e igmpproxy")[2] != 0:
        shell.run_check("apk update && apk add igmpproxy", timeout=300)
    for name, options in FW_RULES.items():
        shell.run_check(f"uci set firewall.{name}=rule")
        shell.run_check(f"uci set firewall.{name}.name='{name} (labgrid-probe)'")
        for key, value in options.items():
            shell.run_check(f"uci set firewall.{name}.{key}='{value}'")
    shell.run_check("uci commit firewall")
    shell.run_check("fw4 reload")
    shell.run_check("/etc/init.d/igmpproxy restart")
    time.sleep(3)
    yield
    shell.run("/etc/init.d/igmpproxy stop")
    for name in FW_RULES:
        shell.run(f"uci -q delete firewall.{name}")
    shell.run("uci commit firewall")
    shell.run("fw4 reload")


def multicast_reaches_lan(slot, record_property, prefix):
    """Join GROUP on the LAN side, send PACKETS from the WAN side, return the
    receiver's counts."""
    slot.mcast_join(group=GROUP, port=PORT, side="lan", igmp_version=2)
    try:
        # igmpproxy adds the route once it has seen the report
        time.sleep(3)
        slot.mcast_send(
            group=GROUP, port=PORT, side="wan", count=PACKETS, interval=INTERVAL_MS
        )
        deadline = time.monotonic() + PACKETS * INTERVAL_MS / 1000 + 10
        result = slot.mcast_result()
        while (result.get("sender") or {}).get("running") and (
            time.monotonic() < deadline
        ):
            time.sleep(1)
            result = slot.mcast_result()
        time.sleep(1)
    finally:
        result = slot.mcast_leave()
    record_property(f"{prefix}_mcast", json.dumps(result))
    sender, receiver = result.get("sender") or {}, result.get("receiver") or {}
    assert sender.get("sent") == PACKETS, f"probe sender: {sender}"
    return receiver


def test_igmp_proxy_forwards(slot, igmpproxy, strategy, record_property):
    """igmpproxy forwards an IPv4 multicast stream from the WAN to a LAN
    client that joined the group. IPTV from an internet provider
    depends on this.

    Regression: openwrt/openwrt#12366

    igmpproxy forwards a group a LAN client joined from WAN to LAN.
    """
    receiver = multicast_reaches_lan(slot, record_property, "igmpproxy")
    received = receiver.get("received", 0)
    if received == 0:
        out = strategy.shell.run(
            "logread -e igmpproxy | tail -n 20; cat /proc/net/ip_mr_cache"
        )[0]
        pytest.fail(
            f"0 of {PACKETS} datagrams to {GROUP} reached the LAN joiner:\n"
            + "\n".join(out)
        )
    assert received >= PACKETS // 2, f"only {received} of {PACKETS} arrived"


@pytest.fixture
def lan_igmp_snooping(strategy, igmpproxy):
    """IGMP snooping on the DUT's br-lan bridge device, restored afterwards."""
    shell = strategy.shell
    out = shell.run_check(
        "uci -q show network | sed -n "
        "\"s/^network\\.\\([^.]*\\)\\.name='br-lan'$/\\1/p\""
    )
    if not out:
        pytest.skip("no br-lan bridge device section in /etc/config/network")
    section = out[0].strip()
    old, _, rc = shell.run(f"uci -q get network.{section}.igmp_snooping")
    old = old[0] if rc == 0 and old else None

    def apply(value):
        if value is None:
            shell.run(f"uci -q delete network.{section}.igmp_snooping")
        else:
            shell.run_check(f"uci set network.{section}.igmp_snooping='{value}'")
        shell.run_check("uci commit network")
        shell.run_check("/etc/init.d/network reload")
        time.sleep(5)
        wait_for_interface(shell, "lan")
        wait_for_interface(shell, "wan", need_ipv4=True)
        shell.run_check("/etc/init.d/igmpproxy restart")
        time.sleep(3)

    apply("1")
    snooping = shell.run("cat /sys/class/net/br-lan/bridge/multicast_snooping")[0]
    yield snooping[0].strip() if snooping else None
    apply(old)


def test_igmp_snooping_bridge(
    slot, igmpproxy, lan_igmp_snooping, strategy, record_property
):
    """With IGMP snooping turned on for the LAN bridge, a LAN client that
    joined a multicast group still gets the group's traffic from the WAN.
    Snooping should only keep multicast away from ports that didn't ask for
    it, never drop it for the ones that did.

    Regression: openwrt/openwrt#9115

    With IGMP snooping on br-lan, the LAN port that joined the group still
    gets the group's traffic (forwarded by igmpproxy from WAN).
    """
    record_property("br_lan_multicast_snooping", lan_igmp_snooping)
    assert lan_igmp_snooping == "1", "igmp_snooping=1 did not reach br-lan"
    receiver = multicast_reaches_lan(slot, record_property, "snooping")
    received = receiver.get("received", 0)
    assert received >= PACKETS // 2, (
        f"{received} of {PACKETS} datagrams reached the LAN joiner with IGMP "
        "snooping on br-lan"
    )
