"""Known, still-open upstream bugs in OpenWrt's userspace services (dnsmasq,
firewall4, wireguard-tools), reproduced on any device, QEMU included.

Each test is marked xfail (not strict) with the upstream issue in its
reason: the CI documents the bug while it is open and shows XPASS once
upstream fixes it, which is the cue to drop the mark.
"""

import json
import time

import pytest
from conftest import ubus_call


def wait_up(shell, iface, timeout=30):
    """True once netifd reports `iface` up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        out, _, rc = shell.run(f"ifstatus {iface}")
        if rc == 0 and json.loads("\n".join(out)).get("up"):
            return True
        time.sleep(2)
    return False


def dnsmasq_running(shell):
    reply = ubus_call(shell, "service", "list", {"name": "dnsmasq"})
    instances = reply.get("dnsmasq", {}).get("instances", {})
    return bool(instances) and all(i.get("running") for i in instances.values())


@pytest.mark.xfail(
    reason="openwrt/openwrt#25409: dnsmasq reload with a changed config kills "
    "the jailed instance, procd gives up after 5 crashes",
    strict=False,
)
def test_dnsmasq_survives_reloads(ssh_command, record_property):
    """dnsmasq still runs and answers DNS after six reloads with a changed
    DHCP lease time. If it fails, saving DHCP settings (e.g. in LuCI) a few
    times leaves the device without DHCP and DNS until dnsmasq is started
    by hand.

    Known issue: openwrt/openwrt#25409

    `reload` restarts the jailed instance and then sends SIGHUP, which can
    hit the new ujail before it has its signal handlers; procd counts that
    as a crash and stops respawning after 5.
    """
    old = ssh_command.run("uci -q get dhcp.lan.leasetime")[0]
    running = []
    try:
        for i in range(6):
            ssh_command.run_check(
                f"uci set dhcp.lan.leasetime={10 + i % 2}h && uci commit dhcp && "
                "/etc/init.d/dnsmasq reload"
            )
            time.sleep(3)
            running.append(dnsmasq_running(ssh_command))
        time.sleep(5)  # give a respawn in flight its time
        final = dnsmasq_running(ssh_command)
        dns, _, rc = ssh_command.run("nslookup localhost 127.0.0.1")
        crashes = ssh_command.run("logread -e dnsmasq | grep -i 'crash loop'")[0]
        record_property("running_after_reloads", json.dumps(running))
        record_property("crash_loop_log", json.dumps(crashes))
        record_property("nslookup", "\n".join(dns))
        assert final, f"dnsmasq not running after 6 reloads ({running}): {crashes}"
        assert rc == 0, "dnsmasq does not answer DNS:\n" + "\n".join(dns)
    finally:
        restore = (
            f"uci set dhcp.lan.leasetime={old[0]}"
            if old
            else "uci -q delete dhcp.lan.leasetime"
        )
        ssh_command.run(f"{restore}; uci commit dhcp; /etc/init.d/dnsmasq restart")


@pytest.mark.xfail(
    reason="openwrt/openwrt#25067: a dhcp section without `dhcpv4 'server'` "
    "silently gets no dhcp-range",
    strict=False,
)
def test_dnsmasq_dhcp_range_without_dhcpv4(ssh_command, record_property):
    """dnsmasq serves DHCP on an interface whose `config dhcp` section has a
    start, limit and lease time but no `dhcpv4` option, as configs written
    before 25.12 do. If it fails, such networks (e.g. a guest network kept
    through an upgrade) silently get no DHCP.

    Known issue: openwrt/openwrt#25067

    The dnsmasq init script now treats a missing `dhcpv4` as `disabled` and
    skips the section without logging anything. Config only: a static
    interface on an empty bridge, then the generated
    /var/etc/dnsmasq.conf.* is checked for its dhcp-range.
    """
    try:
        ssh_command.run_check(
            "uci set network.kitest_br=device && "
            "uci set network.kitest_br.type=bridge && "
            "uci set network.kitest_br.name=br-kitest && "
            "uci set network.kitest_br.bridge_empty=1 && "
            "uci set network.kitest=interface && "
            "uci set network.kitest.device=br-kitest && "
            "uci set network.kitest.proto=static && "
            "uci set network.kitest.ipaddr=192.168.151.1/24 && "
            "uci set dhcp.kitest=dhcp && "
            "uci set dhcp.kitest.interface=kitest && "
            "uci set dhcp.kitest.start=100 && "
            "uci set dhcp.kitest.limit=50 && "
            "uci set dhcp.kitest.leasetime=12h && "
            "uci commit network && uci commit dhcp && reload_config"
        )
        assert wait_up(ssh_command, "kitest"), "interface kitest did not come up"
        ssh_command.run_check("/etc/init.d/dnsmasq restart && sleep 3")
        conf = ssh_command.run("cat /var/etc/dnsmasq.conf.*")[0]
        ranges = [line for line in conf if line.startswith("dhcp-range=")]
        record_property("dhcp_ranges", json.dumps(ranges))
        assert any("192.168.151." in line for line in ranges), (
            f"no dhcp-range for kitest: {ranges}"
        )
    finally:
        ssh_command.run(
            "uci -q delete network.kitest; uci -q delete network.kitest_br; "
            "uci -q delete dhcp.kitest; uci commit network; uci commit dhcp; "
            "reload_config"
        )


@pytest.mark.xfail(
    reason="openwrt/openwrt#11620: `service firewall stop` flushes the whole "
    "nft ruleset, other packages' tables too",
    strict=False,
)
def test_firewall_stop_keeps_foreign_tables(ssh_command, record_property):
    """Stopping the firewall removes only firewall4's own nftables table and
    leaves the tables of other software alone. If it fails, `service firewall
    stop` also wipes the rules of packages like mwan3 or a VPN, and a later
    start does not bring them back.

    Known issue: openwrt/openwrt#11620

    The init script maps `stop` to `fw4 flush`, which flushes the entire
    ruleset; `fw4 stop` would delete only `inet fw4`.
    """
    if ssh_command.run("[ -x /sbin/fw4 ]")[2] != 0:
        pytest.skip("no firewall4 in this image")
    try:
        ssh_command.run_check("nft add table inet kitest")
        ssh_command.run_check("/etc/init.d/firewall stop")
        tables = ssh_command.run_check("nft list tables")
        record_property("tables_after_stop", json.dumps(tables))
        assert "table inet kitest" in tables, f"foreign table flushed: {tables}"
    finally:
        ssh_command.run("/etc/init.d/firewall start")
        ssh_command.run("nft delete table inet kitest")


@pytest.fixture
def wireguard(shell_command):
    """wireguard-tools, installed from the release feeds if the image lacks
    it (skips the test if that fails, e.g. without a WAN uplink). Uses the
    serial console: netifd reads its proto handlers only at start, so it is
    restarted after an install."""
    installed = shell_command.run("apk info -e wireguard-tools")[2] == 0
    if not installed:
        out, _, rc = shell_command.run(
            "apk update >/dev/null 2>&1; apk add wireguard-tools 2>&1", timeout=300
        )
        if rc != 0:
            pytest.skip("cannot install wireguard-tools:\n" + "\n".join(out[-8:]))
        shell_command.run(
            "/etc/init.d/network restart; ubus -t 30 wait_for network.interface.lan",
            timeout=90,
        )
    yield shell_command
    if not installed:
        shell_command.run("apk del wireguard-tools")


def test_wireguard_hex_mtu_and_port_name(wireguard, request, record_property):
    """A WireGuard interface takes a hexadecimal MTU (0x578 = 1400) and a
    service name as listen port (openvpn = 1194), as the 25.12 handler did.
    If it fails, such existing configs silently get MTU 1420 and a random
    port, so peers can't reach the device.

    Known issue: openwrt/openwrt#26538 (main only: regression of the ucode
    proto handler, commit 41bc454)

    The ucode handler parses the MTU with int() ("0x578" -> 0) and gets the
    port through netifd's typed option, which drops names. xfail only where
    the shell handler (wireguard.sh) is gone.
    """
    shell = wireguard
    if shell.run("[ -e /lib/netifd/proto/wireguard.sh ]")[2] != 0:
        request.applymarker(
            pytest.mark.xfail(
                reason="openwrt/openwrt#26538: wireguard ucode proto handler "
                "drops hex mtu and service-name listen_port",
                strict=False,
            )
        )
    try:
        shell.run_check(
            "uci set network.kitest=interface && "
            "uci set network.kitest.proto=wireguard && "
            'uci set network.kitest.private_key="$(wg genkey)" && '
            "uci add_list network.kitest.addresses=10.254.0.1/24 && "
            "uci set network.kitest.mtu=0x578 && "
            "uci set network.kitest.listen_port=openvpn && "
            "uci commit network && reload_config"
        )
        up = wait_up(shell, "kitest")
        mtu = shell.run("cat /sys/class/net/kitest/mtu")[0]
        port = shell.run("wg show kitest listen-port")[0]
        services = shell.run("grep -w openvpn /etc/services")[0]
        record_property("mtu", "\n".join(mtu))
        record_property("listen_port", "\n".join(port))
        assert up, "WireGuard interface kitest did not come up"
        assert mtu == ["1400"], f"mtu 0x578 gave {mtu}"
        assert port == ["1194"], f"listen_port openvpn gave {port} ({services})"
    finally:
        shell.run("uci -q delete network.kitest; uci commit network; reload_config")
