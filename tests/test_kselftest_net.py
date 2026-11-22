"""Linux kernel networking selftests (tools/testing/selftests) on the device,
from the image's `kselftests-net` package, so they match its kernel. Only
tests that need no extra cabling: the bridge/router/tc data path over veth
pairs, the device's own network drivers without a remote end, and a DSA
switch's FDB under concurrent updates.

Images without the package (all official ones) skip the module."""

import json
import re

import pytest

KSFT = "/usr/libexec/kselftests"

# net/forwarding tests on veth pairs (lib.sh's default NETIF_CREATE=yes): the
# kernel's software bridge, router, tc and tunnel paths on this CPU. Not the
# rate-based ones (tc_police, sch_*: flaky on slow CPUs) nor local_termination
# (needs the multicast mtools, not packaged)
FORWARDING = [
    "bridge_fdb_learning_limit.sh",
    "bridge_igmp.sh",
    "bridge_locked_port.sh",
    "bridge_mdb.sh",
    "bridge_mdb_host.sh",
    "bridge_mld.sh",
    "bridge_port_isolation.sh",
    "bridge_sticky_fdb.sh",
    "bridge_vlan_aware.sh",
    "bridge_vlan_mcast.sh",
    "bridge_vlan_unaware.sh",
    "gre_multipath.sh",
    "ipip_flat_gre.sh",
    "no_forwarding.sh",
    "pedit_dsfield.sh",
    "pedit_ip.sh",
    "pedit_l4port.sh",
    "router.sh",
    "router_bridge.sh",
    "router_bridge_vlan.sh",
    "router_multipath.sh",
    "router_nh.sh",
    "router_vid_1.sh",
    "skbedit_priority.sh",
    "tc_actions.sh",
    "tc_chains.sh",
    "tc_flower.sh",
    "tc_shblocks.sh",
    "tc_tunnel_key.sh",
    "tc_vlan_modify.sh",
    "vxlan_bridge_1d.sh",
    "vxlan_bridge_1q.sh",
]

# drivers/net tests with a local device only (NetDrvEnv, no REMOTE_*)
DRIVERS = [
    "hds.py",
    "napi_threaded.py",
    "queues.py",
    "shaper.py",
    "stats.py",
    "hw/irq.py",
    "hw/nic_timestamp.py",
    "hw/rss_api.py",
    "hw/xsk_reconfig.py",
]

# kselftest exit codes; 124 is timeout(1)'s
RC = {0: "pass", 1: "fail", 4: "skip", 124: "timeout"}
KERNEL_ERRORS = re.compile(r"WARNING:|BUG:|Oops|Call trace|Unable to handle")


@pytest.fixture(scope="module")
def ksft(strategy):
    """The installed selftests; restarts the network after the module, as
    the tests move ports into their own bridges and VRFs."""
    # shell_command is function scoped; the strategy's shell outlives a test
    strategy.transition("shell")
    shell = strategy.shell
    if shell.run(f"test -x {KSFT}/run_kselftest.sh")[2]:
        pytest.skip("image without kselftests-net")
    yield shell
    shell.run("/etc/init.d/network restart; sleep 10", timeout=120)


@pytest.fixture(scope="module")
def links(ksft):
    """`ip -d -j link` by name."""
    return {
        link["ifname"]: link
        for link in json.loads("\n".join(ksft.run_check("ip -d -j link show")))
    }


def kind(link):
    return link.get("linkinfo", {}).get("info_kind")


@pytest.fixture(scope="module")
def netdevs(ksft, links):
    """The device's Ethernet interfaces with a link (the lab's LAN and WAN
    cables), plus the conduit of a DSA port among them: the CPU's own MAC,
    whose driver the DSA ports only wrap."""
    found = []
    for name, link in links.items():
        if kind(link) not in (None, "dsa") or link.get("link_type") != "ether":
            continue
        if ksft.run(f"cat /sys/class/net/{name}/carrier")[0] != ["1"]:
            continue
        found.append(name)
        if kind(link) == "dsa":
            found.append(link["linkinfo"]["info_data"]["conduit"])
    return sorted(set(found))


def kernel_log_lines(shell):
    return int(shell.run_check("dmesg | wc -l")[0])


def run_ksft(shell, cwd, command, timeout, record_property):
    """Run one selftest; returns its result (RC) and the new kernel warnings."""
    before = kernel_log_lines(shell)
    _, _, rc = shell.run(
        f"cd {KSFT}/{cwd} && timeout {timeout} {command} >/tmp/ksft.log 2>&1",
        timeout=timeout + 60,
    )
    # TEST: lines of lib.sh, (not )ok lines of KTAP
    lines = shell.run(
        r"grep -E '^(TEST: |not ok |ok |# Totals)' /tmp/ksft.log"
        r" | grep -vE '\[ OK \]$|^ok '"
    )[0]
    warnings = shell.run(
        f"dmesg | tail -n +{before + 1} | grep -E '{KERNEL_ERRORS.pattern}'"
    )[0]
    shell.run("rm -f /tmp/ksft.log")
    result = RC.get(rc, f"rc {rc}")
    record_property("kselftest_result", result)
    record_property("kselftest_details", json.dumps(lines))
    record_property("kernel_warnings", json.dumps(warnings))
    return result, lines, warnings


def check(result, lines, warnings):
    assert warnings == [], "kernel warnings during the test"
    if result == "skip":
        pytest.skip("; ".join(lines) or "skipped by the selftest")
    assert result == "pass", "\n".join(lines)


@pytest.mark.parametrize("test", FORWARDING)
def test_kselftest_forwarding(ksft, test, record_property):
    """A net/forwarding selftest on veth pairs passes: the kernel's software
    bridge, routing, tc and tunnel paths behave as upstream expects on this
    CPU (endianness, alignment, word size). A failure the same kernel passes
    on other targets points to an arch or OpenWrt patch problem."""
    check(*run_ksft(ksft, "net/forwarding", f"./{test}", 900, record_property))


@pytest.mark.parametrize("test", DRIVERS)
def test_kselftest_drivers_net(ksft, netdevs, test, record_property):
    """A drivers/net selftest passes on each Ethernet interface with a link
    (LAN, WAN, DSA conduit): statistics, queues, NAPI, IRQs, timestamping as
    the driver reports them through netlink and ethtool. A failure is a
    driver that reports inconsistent counters or breaks on reconfiguration;
    a skip is a feature the driver doesn't have."""
    record_property("kselftest_netdevs", json.dumps(netdevs))
    results = {}
    for dev in netdevs:
        result, lines, warnings = run_ksft(
            ksft,
            "drivers/net",
            f"env NETIF={dev} python3 ./{test}",
            600,
            record_property,
        )
        results[dev] = (result, lines, warnings)
    record_property(
        "kselftest_results", json.dumps({d: r[0] for d, r in results.items()})
    )
    failed = {d: r for d, r in results.items() if r[0] not in ("pass", "skip") or r[2]}
    assert failed == {}, failed
    if all(r[0] == "skip" for r in results.values()):
        pytest.skip(f"skipped on {', '.join(results) or 'no interface with a link'}")


def test_kselftest_dsa_fdb_stress(ksft, links, record_property):
    """Adding and removing an FDB entry on a DSA port in a tight loop while
    the FDB is dumped (drivers/net/dsa/test_bridge_fdb_stress.sh) leaves no
    kernel warning: the switch driver's FDB code is safe without the rtnl
    lock. Uses a DSA port without a link, so the lab's cables stay as they
    are."""
    free = [
        name
        for name, link in links.items()
        if kind(link) == "dsa"
        and ksft.run(f"cat /sys/class/net/{name}/carrier")[0] != ["1"]
    ]
    if not free:
        pytest.skip("no DSA port without a link")
    record_property("dsa_port", free[0])
    check(
        *run_ksft(
            ksft,
            "drivers/net/dsa",
            f"./test_bridge_fdb_stress.sh {free[0]}",
            300,
            record_property,
        )
    )
