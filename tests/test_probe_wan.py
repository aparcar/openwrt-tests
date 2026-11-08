"""
WAN-side services of the labgrid-probe (contrib/labgrid-probe) against the
DUT's WAN client: DHCPv4 options and NTP from the probe's WAN namespace.

The probe serves the DUT's WAN lease (dnsmasq in its WAN namespace); the
wan_dhcp method restarts that server with extra options and an NTP server,
and the DUT picks them up when it renews (here: ifup wan).
"""

# ruff: noqa: F811 (tests take the imported probe fixtures as arguments)

import datetime
import ipaddress
import json
import re
import time

import pytest
from probe_fixtures import (
    slot,  # noqa: F401 (fixture)
    wait_for_interface,
    wan_slot,  # noqa: F401 (fixture)
)

pytestmark = pytest.mark.lg_feature("wan_port")


def restart_wan(shell, timeout=120):
    """Take the DUT's WAN down and up, so it requests a fresh lease."""
    shell.run_check("ifup wan")
    time.sleep(3)
    return wait_for_interface(shell, "wan", timeout=timeout, need_ipv4=True)


@pytest.fixture
def wan_dhcp_reset(wan_slot, shell_command):
    """Restores the default WAN DHCP server (no extra options, no NTP)."""
    yield wan_slot
    wan_slot.wan_dhcp(options=[], ntp=False)
    restart_wan(shell_command)


def wan_broadcast(shell, device):
    """The broadcast address of the DUT's WAN IPv4 address, or None."""
    out, _, rc = shell.run(f"ip -4 addr show dev {device}")
    match = re.search(r"\binet \S+ brd (\S+)", "\n".join(out)) if rc == 0 else None
    if match:
        return match.group(1)
    out, _, _ = shell.run(f"ifconfig {device}")
    match = re.search(r"Bcast:(\S+)", "\n".join(out))
    return match.group(1) if match else None


@pytest.mark.xfail(
    reason="openwrt/openwrt#24782: dhcp.script ignores DHCP option 28 "
    "(broadcast derived from the netmask, 25.12.6)"
)
def test_wan_dhcp_broadcast_option(wan_dhcp_reset, shell_command, record_property):
    """Regression: openwrt/openwrt#24782

    The DUT's WAN address uses the broadcast address the DHCP server sends
    in option 28, not the one derived from the netmask.
    """
    wan_ip = ipaddress.ip_interface(f"{wan_dhcp_reset.info['wan_ip']}/24")
    derived = wan_ip.network.broadcast_address
    # Same /24, but a broadcast the netmask doesn't give
    custom = str(derived - 128)
    wan_dhcp_reset.wan_dhcp(options=[f"28,{custom}"])

    status = restart_wan(shell_command)
    device = status.get("l3_device") or status.get("device")
    brd = wan_broadcast(shell_command, device)
    record_property("wan_broadcast", brd)
    assert brd == custom, (
        f"{device} broadcast is {brd}, DHCP option 28 sent {custom} "
        f"(netmask-derived: {derived})"
    )


@pytest.fixture
def only_dhcp_ntp(shell_command):
    """sysntpd without its configured servers, so only the one from DHCP is
    left: the DUT has internet through the probe and would otherwise sync
    from the default pool. Restored afterwards."""
    shell = shell_command
    out, _, rc = shell.run("uci -q get system.ntp.server")
    servers = out[0].split() if rc == 0 and out else []
    shell.run("uci -q delete system.ntp.server && uci commit system")
    shell.run_check("/etc/init.d/sysntpd restart")
    yield
    for server in servers:
        shell.run_check(f"uci add_list system.ntp.server='{server}'")
    shell.run_check("uci commit system")
    shell.run_check("/etc/init.d/sysntpd restart")


def dut_clock_offset(shell):
    """DUT clock minus this host's clock, in seconds."""
    dut = int(shell.run_check("date +%s")[-1].strip())
    return dut - time.time()


def test_ntp_synced_from_wan_dhcp(
    wan_dhcp_reset, only_dhcp_ntp, shell_command, record_property
):
    """Regression: openwrt/openwrt#10843

    sysntpd syncs to the NTP server the WAN DHCP server announces (option
    42), with its configured servers removed. The DUT's clock is set back a
    day first; it has to be within 10 s of this host's clock again after the
    WAN lease renews.
    """
    reply = wan_dhcp_reset.wan_dhcp(ntp=True)
    server = reply["ntp_server"]

    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)
    shell_command.run_check(f"date -u -s '{past:%Y-%m-%d %H:%M:%S}'")
    status = restart_wan(shell_command)
    # dhcp.script passes option 42 on as interface data; informational
    record_property("wan_dhcp_data", json.dumps(status.get("data", {})))

    deadline = time.monotonic() + 120
    offset = dut_clock_offset(shell_command)
    while abs(offset) > 10 and time.monotonic() < deadline:
        time.sleep(5)
        offset = dut_clock_offset(shell_command)
    record_property("ntp_offset_s", round(offset, 1))
    if abs(offset) > 10:
        out = shell_command.run("logread -e ntp | tail -n 20")[0]
        pytest.fail(
            f"DUT clock still {offset:.0f}s off after 120s with NTP server "
            f"{server} (interface data {status.get('data')}):\n" + "\n".join(out)
        )
