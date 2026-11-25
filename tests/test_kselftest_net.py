"""Linux kernel networking selftests (tools/testing/selftests) on the device,
from the image's `kselftests-net` package, so they match its kernel: the
bridge/router/tc data path over veth pairs, the device's own network drivers
without a remote end, and a DSA switch's FDB under concurrent updates. Where
the target declares loop cables (`kselftest_loops`), also the bridge/tc tests
offloaded to the DSA switch.

Images without the package (all official ones) skip the module."""

import json
import re
from pathlib import Path

import pytest

KSFT = "/usr/libexec/kselftests"

# The selftests the kselftests-net package installs ("collection:test", its
# kselftest-list.txt): refresh with scripts/ksft-list.sh after a package
# change. Which ones run, and how, follows from the collection; a test missing
# from the image under test skips.
TESTS = [
    tuple(line.split(":", 1))
    for line in (Path(__file__).parent / "kselftest-net.list").read_text().split()
]

# The exceptions: tests that can't run on OpenWrt, with the reason
EXCLUDE = {
    "drivers/net:shaper.py": "the net-shaper family (CONFIG_NET_SHAPER) is only "
    "built when a driver selects it, none in OpenWrt's targets do",
}

# Selftest timeouts (s) above the default 900: no_forwarding waits out ~200
# unanswered pings (5 s each) and reads the capture once per check (~350
# tcpdump runs): 17 min on a BPi-R4 Lite (Cortex-A53)
SLOW = {"no_forwarding.sh": 2700}

# Known failures per device (profile), marked xfail (not strict) at run time,
# so a fix shows as XPASS; other devices run these as normal tests
RTL83XX_FDB = (
    "rtl83xx DSA has no FDB isolation: a MAC learned on a bridge port drops "
    "known unicast to a standalone port instead of trapping it to the CPU"
)
SLOW_CPU = "500 MHz CPU: the selftest's timing doesn't hold"
KNOWN = {
    "zyxel_gs1900-8hp-b1": {
        **{
            f"test_kselftest_dsa_loops[{test}]": RTL83XX_FDB
            for test in (
                "bridge_locked_port.sh",
                "bridge_mdb.sh",
                "bridge_mld.sh",
                "bridge_vlan_aware.sh",
                "bridge_vlan_unaware.sh",
            )
        },
        "test_kselftest_dsa_loops[tc_actions.sh]": f"{RTL83XX_FDB}; times out",
        "test_kselftest_forwarding[bridge_igmp.sh]": f"{SLOW_CPU} (exclude timeout)",
        "test_kselftest_forwarding[bridge_mld.sh]": f"{SLOW_CPU} (exclude timeout)",
        "test_kselftest_forwarding[tc_actions.sh]": f"{SLOW_CPU} (900 s timeout)",
        "test_kselftest_drivers_net[queues.py]": "single-core kernel without "
        "RPS: no sysfs rx-* queues for get_queues/addremove_queues to count",
        "test_kselftest_drivers_net[hw/irq.py]": "rtl838x eth doesn't set the "
        "NAPI IRQ (netif_napi_set_irq), check_irqs_reported finds none",
    },
}


@pytest.fixture(autouse=True)
def known_issue(env, request):
    profile = (env.config.data.get("openwrt") or {}).get("profile")
    if reason := KNOWN.get(profile, {}).get(request.node.name):
        request.applymarker(pytest.mark.xfail(reason=reason))


def params(collections, exclude=(), timeout=None):
    """pytest params for the list's tests in `collections`, named by their
    path below the first one (hw/irq.py); excluded ones skip with the reason.
    pytest-timeout's default (300 s) would abort a selftest that still runs
    on the console, so each test gets its selftest timeout plus the result
    collection."""
    found = []
    for collection, test in TESTS:
        if collection not in collections or test in exclude:
            continue
        name = f"{collection}/{test}".removeprefix(f"{collections[0]}/")
        marks = [pytest.mark.timeout(timeout or SLOW.get(test, 900) + 100)]
        if reason := EXCLUDE.get(f"{collection}:{test}"):
            marks.append(pytest.mark.skip(reason=reason))
        found.append(pytest.param((collection, test), id=name, marks=marks))
    return found


def require(shell, collection, test):
    """Skip a test the image under test doesn't have (older package)."""
    if shell.run(f"test -e {KSFT}/{collection}/{test}")[2]:
        pytest.skip(f"{collection}/{test} not in this image's kselftests-net")


# kselftest exit codes; 124 is timeout(1)'s
RC = {0: "pass", 1: "fail", 4: "skip", 124: "timeout"}
KERNEL_ERRORS = re.compile(r"WARNING:|BUG:|Oops|Call trace|Unable to handle")


@pytest.fixture(scope="module")
def ksft(strategy):
    """The installed selftests, with the firewall stopped: fw4 drops and
    rejects traffic on interfaces outside its zones, which are all the veths
    and VRFs the selftests create. Also stops the system's smcrouted (the
    smcroute package starts one): it holds the kernel's multicast routing
    socket, so the one router.sh starts gets no VIFs. Both are disabled
    while the module runs, as the iface hotplug script reloads an enabled
    firewall on every ifup (a late WAN DHCP lease). Restarts the network
    and the services that were enabled after the module, as the tests move
    ports into their own bridges and VRFs."""
    # shell_command is function scoped; the strategy's shell outlives a test
    strategy.transition("shell")
    shell = strategy.shell
    if shell.run(f"test -x {KSFT}/run_kselftest.sh")[2]:
        pytest.skip("image without kselftests-net")
    # On slow devices the shell comes before netifd has set up the bridges:
    # its later conduit MTU change takes eth0 and with it every DSA port down,
    # also those a running selftest uses (GS1900: 7 s after the prompt)
    shell.run(
        "i=0; until logread | grep -q 'init complete' || [ $i -ge 180 ];"
        " do sleep 1; i=$((i+1)); done",
        timeout=200,
    )
    # the tests' interface churn logs to the serial console (VisionFive 2)
    # and garbles the shell driver's markers; warnings are read from dmesg
    loglevel = shell.run_check("cat /proc/sys/kernel/printk")[0].split()[0]
    shell.run("dmesg -n 1")
    enabled = [
        s
        for s in ("firewall", "smcroute")
        if shell.run(f"/etc/init.d/{s} enabled")[2] == 0
    ]
    for s in enabled:
        shell.run(f"/etc/init.d/{s} disable; /etc/init.d/{s} stop")
    yield shell
    shell.run("/etc/init.d/network restart; sleep 10", timeout=120)
    for s in enabled:
        shell.run(f"/etc/init.d/{s} enable; /etc/init.d/{s} start")
    shell.run(f"dmesg -n {loglevel}")


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
        # </dev/null: background helpers (ncat in tc_actions) would read the serial console
        f"cd {KSFT}/{cwd} && timeout {timeout} {command} </dev/null >/tmp/ksft.log 2>&1",
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
    result = RC.get(rc, f"rc {rc}")
    if result != "pass":
        # the selftest prints the failing command's error just before its TEST line
        record_property(
            "kselftest_log", json.dumps(shell.run("tail -n 60 /tmp/ksft.log")[0])
        )
    shell.run("rm -f /tmp/ksft.log")
    record_property("kselftest_result", result)
    record_property("kselftest_details", json.dumps(lines))
    record_property("kernel_warnings", json.dumps(warnings))
    return result, lines, warnings


def check(result, lines, warnings):
    assert warnings == [], "kernel warnings during the test"
    if result == "skip":
        pytest.skip("; ".join(lines) or "skipped by the selftest")
    assert result == "pass", "\n".join(lines)


@pytest.mark.parametrize("case", params(["net/forwarding"]))
def test_kselftest_forwarding(ksft, case, record_property):
    """A net/forwarding selftest on veth pairs passes: the kernel's software
    bridge, routing, tc and tunnel paths behave as upstream expects on this
    CPU (endianness, alignment, word size). A failure the same kernel passes
    on other targets points to an arch or OpenWrt patch problem.

    lib.sh's default NETIF_CREATE=yes creates the veth pairs."""
    collection, test = case
    require(ksft, collection, test)
    # busybox's ping6 fails the selftests' pings; iputils (a dependency) works
    command = f"env PING6='ping -6' ./{test}"
    timeout = SLOW.get(test, 900)
    check(*run_ksft(ksft, collection, command, timeout, record_property))


@pytest.mark.parametrize(
    "case", params(["drivers/net", "drivers/net/hw"], timeout=2400)
)
def test_kselftest_drivers_net(ksft, netdevs, case, record_property):
    """A drivers/net selftest passes on each Ethernet interface with a link
    (LAN, WAN, DSA conduit): statistics, queues, NAPI, IRQs, timestamping as
    the driver reports them through netlink and ethtool. A failure is a
    driver that reports inconsistent counters or breaks on reconfiguration;
    a skip is a feature the driver doesn't have.

    Tests with a remote end (NetDrvEpEnv: traffic to a second host over
    REMOTE_*) skip: the lab has no remote host for them yet."""
    collection, test = case
    require(ksft, collection, test)
    if ksft.run(f"grep -q NetDrvEpEnv {KSFT}/{collection}/{test}")[2] == 0:
        pytest.skip("needs a remote end (NetDrvEpEnv, REMOTE_*)")
    record_property("kselftest_netdevs", json.dumps(netdevs))
    results = {}
    for dev in netdevs:
        # A selftest taking the DSA conduit down (queues.py check_down) leaves
        # all switch ports down; it brings only its own NETIF back up and
        # waits 5 s for carrier, which a port looped to another port never
        # gets. Bring them all up and wait for the renegotiation here.
        ups = "; ".join(f"ip link set dev {d} up" for d in netdevs)
        carrier = f'[ "$(cat /sys/class/net/{dev}/carrier)" = 1 ]'
        _, _, rc = ksft.run(
            f"{ups}; i=0;"
            f" while ! {carrier} && [ $i -lt 30 ]; do sleep 1; i=$((i+1)); done;"
            f" {carrier}",
            timeout=60,
        )
        if rc != 0:
            results[dev] = ("no carrier", ["no carrier within 30 s"], [])
            continue
        # from the collection's directory, as run_kselftest.sh does: hw/ has
        # its own lib/py that pulls in drivers/net's
        result, lines, warnings = run_ksft(
            ksft, collection, f"env NETIF={dev} python3 ./{test}", 600, record_property
        )
        results[dev] = (result, lines, warnings)
    record_property(
        "kselftest_results", json.dumps({d: r[0] for d, r in results.items()})
    )
    failed = {d: r for d, r in results.items() if r[0] not in ("pass", "skip") or r[2]}
    assert failed == {}, failed
    if all(r[0] == "skip" for r in results.values()):
        pytest.skip(f"skipped on {', '.join(results) or 'no interface with a link'}")


# drivers/net/dsa's own test; the collection's other entries are wrappers of
# net/forwarding tests (test_kselftest_dsa_loops)
FDB_STRESS = "test_bridge_fdb_stress.sh"


@pytest.mark.timeout(400)
def test_kselftest_dsa_fdb_stress(ksft, links, record_property):
    """Adding and removing an FDB entry on a DSA port in a tight loop while
    the FDB is dumped (drivers/net/dsa/test_bridge_fdb_stress.sh) leaves no
    kernel warning: the switch driver's FDB code is safe without the rtnl
    lock. Uses a DSA port without a link, so the lab's cables stay as they
    are."""
    require(ksft, "drivers/net/dsa", FDB_STRESS)
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
            f"./{FDB_STRESS} {free[0]}",
            300,
            record_property,
        )
    )


@pytest.fixture(scope="module")
def loop_ports(env, ksft, links):
    """The ports of the target's `openwrt: kselftest_loops: [h1, swp1, swp2,
    h2]`, cabled h1<->swp1 and swp2<->h2, with swp1/swp2 on the DSA switch.
    The image must keep them out of every bridge (uci-defaults): bridged,
    the loops would storm or join the lab's networks."""
    ports = (env.config.data.get("openwrt") or {}).get("kselftest_loops")
    if not ports:
        pytest.skip("target defines no kselftest_loops")
    assert len(ports) == 4, "kselftest_loops needs [h1, swp1, swp2, h2]"
    for port in ports:
        assert port in links, f"{port} doesn't exist"
        assert "master" not in links[port], (
            f"{port} is in {links[port]['master']}: the loops need unbridged ports"
        )
    assert kind(links[ports[1]]) == "dsa" and kind(links[ports[2]]) == "dsa", (
        "swp1 and swp2 must be DSA ports"
    )
    # a short command line: slow serial consoles (GS1900) drop long input
    names = " ".join(ports)
    _, _, rc = ksft.run(
        f"up() {{ for p in {names}; do [ $(cat /sys/class/net/$p/carrier) = 1 ]"
        f" || return 1; done; }}; for p in {names}; do ip link set $p up; done;"
        " i=0; until up || [ $i -ge 30 ]; do sleep 1; i=$((i+1)); done; up",
        timeout=60,
    )
    if rc != 0:
        pytest.skip(f"no carrier on all of {ports} within 30 s: loops not cabled")
    return ports


@pytest.mark.parametrize("case", params(["drivers/net/dsa"], exclude=(FDB_STRESS,)))
def test_kselftest_dsa_loops(ksft, loop_ports, case, record_property):
    """A net/forwarding selftest passes over loop cables through the DSA
    switch (drivers/net/dsa): the switch driver offloads bridging, FDB
    learning, VLAN filtering, multicast and tc as the software bridge does
    it. A failure is a switch driver that forwards, filters or floods
    differently from the kernel's bridge.

    The collection's wrappers run the net/forwarding test with
    NETIF_CREATE=no on the four given ports."""
    collection, test = case
    record_property("kselftest_loops", json.dumps(loop_ports))
    require(ksft, collection, test)
    command = f"env PING6='ping -6' ./{test} {' '.join(loop_ports)}"
    timeout = SLOW.get(test, 900)
    check(*run_ksft(ksft, collection, command, timeout, record_property))
