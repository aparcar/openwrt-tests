"""
Known, still open upstream networking bugs, reproduced on the lab devices.

Each test checks the behaviour a user expects and is marked xfail (not
strict) where the bug applies, with the issue in the reason: the CI shows
the bug as XFAIL while it exists and as XPASS once a fix lands. A test
whose setup fails before the bug is triggered fails normally.

LAN-side checks run through the labgrid-probe (contrib/labgrid-probe):
its LAN client namespace pings the DUT and captures its Router
Advertisements. Every test restores the configuration it changes.
"""

# ruff: noqa: F811 (tests take the imported probe fixtures as arguments)

import ipaddress
import json
import logging
import re
import time

import pytest
from probe_fixtures import (
    DUT_LAN_IP,
    lease,  # noqa: F401 (fixture)
    online,  # noqa: F401 (fixture, installs missing tools)
    slot,  # noqa: F401 (fixture)
    wait_for_interface,
    wan_slot,  # noqa: F401 (fixture, needed by online)
)
from test_probe_ra import UciChanges
from test_wifi import (
    WIFI_BANDS,
    band_params,
    find_ap,
    open_ap,
    wifi_radio_for_band,
    wifi_settle,
)

logger = logging.getLogger(__name__)


def xfail_here(request, reason):
    """Expect the rest of the test to fail (non-strict: XPASS once fixed).
    Applied once the setup has worked, so a broken setup still fails."""
    request.applymarker(pytest.mark.xfail(reason=reason))


# --- openwrt/openwrt#26131: MT753x blackholes a MAC seen on a foreign port ------

MT753X_REASON = (
    "openwrt/openwrt#26131 (#24760, #25305): MT753x DSA keeps a host's MAC on "
    "the CPU port after br-lan learned it on a non-switch port (25.12, snapshot)"
)
# Port pair of the foreign (non-switch) bridge port
VETH, VETH_PEER = "kifb0", "kifb1"
RECOVERY_TIMEOUT = 5
# Bridge ageing time plus margin: how long a stale entry can last
AGEING_TIMEOUT = 330


def mt753x_dsa(shell):
    """Whether a DSA switch port of the DUT is driven by mt7530 (MT7530,
    MT7531, MT7988 built-in switch: mt7530-mdio / mt7530-mmio)."""
    out = shell.run("ls -l /sys/class/net/*/device/driver")[0]
    return any("mt7530" in line for line in out)


def probe_reaches_dut(slot):
    return slot.call("ping", host=DUT_LAN_IP, count=1)["received"] > 0


@pytest.fixture
def foreign_port(request, slot, shell_command):
    """A veth pair on the DUT, not in any bridge yet. Installs kmod-veth,
    ip-full and ip-bridge over the WAN if the image lacks them (skips
    without a cabled WAN port). Removed afterwards; the probe's MAC may stay
    blackholed for the bridge ageing time, so teardown waits for that."""
    shell = shell_command
    create = f"ip link add {VETH} type veth peer name {VETH_PEER}"
    if shell.run("command -v bridge")[2] != 0 or shell.run(create)[2] != 0:
        request.getfixturevalue("online")
        shell.run_check(
            "apk update && apk add kmod-veth ip-full ip-bridge", timeout=300
        )
        shell.run(f"ip link del {VETH}")
        shell.run_check(create)
    yield VETH, VETH_PEER
    shell.run(f"ip link del {VETH}")
    deadline = time.monotonic() + AGEING_TIMEOUT
    while not probe_reaches_dut(slot):
        if time.monotonic() > deadline:
            logger.warning("probe still can't reach the DUT %ds after", AGEING_TIMEOUT)
            break
        time.sleep(5)


@pytest.mark.timeout(300 + AGEING_TIMEOUT)
def test_bridge_mac_returns_from_foreign_port(
    request, slot, lease, foreign_port, shell_command, record_property
):
    """A LAN client stays reachable after its MAC address was briefly seen
    on a bridge port that is not a switch port (Wi-Fi, a VPN tap, a
    reflecting WAN). On MT753x switches the client is cut off for minutes.
    This is a known bug on MT753x DSA switches, expected to fail there.

    Regression: openwrt/openwrt#26131

    A veth in br-lan sends with the probe LAN client's MAC, so the bridge
    learns that MAC on the veth; then the veth is deleted. The probe has to
    ping the DUT again within 5 s.
    """
    shell = shell_command
    veth, peer = foreign_port
    mac = slot.info["lan_mac"].lower()
    assert probe_reaches_dut(slot), "the probe can't reach the DUT before the test"

    shell.run_check(f"ip link set {veth} master br-lan up")
    shell.run_check(f"ip link set {peer} address {mac}")
    # send right away from the link-local address, without waiting for DAD
    shell.run_check(f"echo 0 > /proc/sys/net/ipv6/conf/{peer}/accept_dad")
    shell.run_check(f"ip link set {peer} up")
    learned = []
    deadline = time.monotonic() + 10
    while not learned and time.monotonic() < deadline:
        shell.run(f"ping -6 -c 1 -W 1 -I {peer} ff02::1")
        fdb = shell.run("bridge fdb show br br-lan")[0]
        learned = [line for line in fdb if line.startswith(f"{mac} dev {veth}")]
    assert learned, f"br-lan didn't learn {mac} on {veth}"

    if mt753x_dsa(shell):
        xfail_here(request, MT753X_REASON)
    shell.run_check(f"ip link del {veth}")
    start = time.monotonic()
    reachable = False
    while not reachable and time.monotonic() - start < RECOVERY_TIMEOUT:
        reachable = probe_reaches_dut(slot)
    elapsed = time.monotonic() - start
    record_property("foreign_mac_recovery_s", round(elapsed, 1))
    fdb = [line for line in shell.run("bridge fdb show")[0] if mac in line]
    record_property("foreign_mac_fdb", json.dumps(fdb))
    assert reachable, (
        f"probe ({mac}) can't reach the DUT {elapsed:.0f}s after its MAC left "
        "the foreign bridge port; fdb:\n" + "\n".join(fdb)
    )


# --- odhcpd#387: RFC 9096 deprecates a prefix that came back ---------------------

ODHCPD_387_REASON = (
    "openwrt/odhcpd#387: RFC 9096 stale prefix loop after the upstream "
    "reconnects with the same prefix and ip6ifaceid random (fix PR "
    "openwrt/odhcpd#438 unmerged, 25.12.6)"
)
# A static "upstream" with a routed prefix stands in for a WAN6 with DHCPv6-PD
UPSTREAM = "kiup"
DELEGATED = ipaddress.ip_network("2001:db8:387::/48")


@pytest.fixture
def delegated_prefix(shell_command):
    """A static interface on lo that routes DELEGATED to the DUT (as
    `ip6prefix`, like a WAN6 with prefix delegation) and random interface IDs
    on the LAN. The network config is restored afterwards."""
    shell = shell_command
    uci = UciChanges(shell)
    uci.set(f"network.{UPSTREAM}", "interface")
    uci.set(f"network.{UPSTREAM}.proto", "static")
    uci.set(f"network.{UPSTREAM}.device", "lo")
    uci.set(f"network.{UPSTREAM}.ip6addr", f"{DELEGATED[1]}/128")
    uci.set(f"network.{UPSTREAM}.ip6prefix", str(DELEGATED))
    uci.set("network.lan.ip6ifaceid", "random")
    shell.run_check("/etc/init.d/network reload")
    yield
    uci.restore()
    shell.run_check("/etc/init.d/network reload")
    wait_for_interface(shell, "lan", need_ipv4=True)


def lan_assignment(shell, timeout=60):
    """The /64 of DELEGATED the LAN has, waiting up to timeout s for it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = wait_for_interface(shell, "lan")
        for a in status.get("ipv6-prefix-assignment", []):
            net = ipaddress.ip_network(f"{a['address']}/{a['mask']}", strict=False)
            if net.subnet_of(DELEGATED):
                return net
        time.sleep(3)
    pytest.fail(f"the LAN got no prefix of {DELEGATED} within {timeout}s")


def test_ra_prefix_survives_upstream_reconnect(
    request, slot, lease, delegated_prefix, shell_command, record_property
):
    """After the upstream connection drops briefly and comes back with the
    same delegated prefix, the DUT keeps announcing that prefix on the LAN as
    usable. Otherwise LAN clients deprecate their global IPv6 addresses and
    keep only the ULA. This is a known bug, expected to fail.

    Regression: openwrt/odhcpd#387

    With `ip6ifaceid random` on the LAN, the upstream interface is restarted
    twice (the LAN address changes, its /64 doesn't). Every RA within the
    next 10 s has the LAN's /64 with a preferred and valid lifetime > 0.
    """
    shell = shell_command
    before = lan_assignment(shell)
    xfail_here(request, ODHCPD_387_REASON)
    for _ in range(2):
        shell.run_check(f"ifup {UPSTREAM}")
        time.sleep(10)
    after = lan_assignment(shell)
    record_property("lan_prefix", json.dumps([str(before), str(after)]))

    ras = slot.ra_capture(timeout=10, count=0)["ras"]
    pios = [
        p
        for ra in ras
        for p in ra["prefixes"]
        if ipaddress.ip_network(f"{p['prefix']}/{p['length']}", strict=False) == after
    ]
    record_property("ra_pios", json.dumps(pios))
    assert pios, f"no RA with {after} within 10 s: {ras}"
    stale = [p for p in pios if not p["preferred_lifetime"] or not p["valid_lifetime"]]
    assert not stale, f"{after} announced as deprecated after the reconnect: {stale}"


# --- openwrt/openwrt#25646: flowtable loses Wi-Fi after wifi down/up -------------

FLOWTABLE_REASON = (
    "openwrt/openwrt#25646: fw4 doesn't add a recreated Wi-Fi interface to the "
    "flowtable after `wifi down; wifi up` (firewall4 PR #127 / PR #25650 open)"
)
OFFLOAD_OPTIONS = ("flow_offloading", "flow_offloading_hw")


@pytest.fixture
def offload_restore(ssh_command):
    """Restores the wireless and firewall config and reloads both."""
    for config in ("wireless", "firewall"):
        ssh_command.run_check(f"cp /etc/config/{config} /tmp/{config}.pre-test")
    yield
    for config in ("wireless", "firewall"):
        ssh_command.run(f"uci revert {config}")
        ssh_command.run(f"cp /tmp/{config}.pre-test /etc/config/{config}")
    ssh_command.run("wifi", timeout=60)
    wifi_settle(ssh_command)
    ssh_command.run("/etc/init.d/firewall reload")


def in_flowtable(shell, ifname, timeout=15):
    """Whether `nft list flowtables` lists ifname within timeout s."""
    deadline = time.monotonic() + timeout
    while True:
        out = "\n".join(shell.run("nft list flowtables")[0])
        if re.search(rf"(?<![\w-]){re.escape(ifname)}(?![\w-])", out):
            return True, out
        if time.monotonic() > deadline:
            return False, out
        time.sleep(2)


@pytest.mark.parametrize("band", band_params())
def test_flowtable_keeps_wifi_after_restart(
    request, ssh_command, offload_restore, band, record_property
):
    """With flow offloading on, a Wi-Fi access point stays in the firewall's
    flowtable after the radio is restarted (`wifi down`, `wifi up`), as
    after a settings change or a radio reset. Otherwise traffic of Wi-Fi
    clients silently stops being offloaded. This is a known bug, expected to
    fail.

    Regression: openwrt/openwrt#25646

    With flow_offloading and flow_offloading_hw on, the AP's interface is in
    `nft list flowtables`; after `wifi down <radio>; wifi up <radio>` it has
    to be in it again within 15 s of the AP coming back.
    """
    shell = ssh_command
    ssid = f"OpenWrt-CI-{band}"
    ifname = open_ap(shell, band, WIFI_BANDS[band]["encryptions"][0], ssid=ssid)
    for option in OFFLOAD_OPTIONS:
        shell.run_check(f"uci set firewall.@defaults[0].{option}=1")
    shell.run_check("uci commit firewall")
    shell.run_check("/etc/init.d/firewall reload")
    found, out = in_flowtable(shell, ifname)
    assert found, f"{ifname} not in the flowtable with offloading on:\n{out}"

    xfail_here(request, FLOWTABLE_REASON)
    radio, _ = wifi_radio_for_band(shell, band)
    shell.run_check(f"wifi down {radio}", timeout=60)
    time.sleep(3)
    shell.run_check(f"wifi up {radio}", timeout=60)
    deadline = time.monotonic() + 60
    ifname, _ = find_ap(shell, ssid)
    while not ifname and time.monotonic() < deadline:
        time.sleep(2)
        ifname, _ = find_ap(shell, ssid)
    assert ifname, f"the {ssid} AP didn't come back after wifi up {radio}"

    found, out = in_flowtable(shell, ifname)
    record_property("flowtables", out)
    assert found, f"{ifname} missing from the flowtable after wifi down/up:\n{out}"
