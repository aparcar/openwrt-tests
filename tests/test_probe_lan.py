"""
The DUT as a router, seen from a real client on its LAN port: addresses by
DHCP and SLAAC, DNS, reaching the internet through the DUT, and throughput.

Runs through the labgrid-probe (contrib/labgrid-probe).

The probe puts one network namespace on the DUT's LAN VLAN (DHCP/DNS/SLAAC
client, iperf3 client) and one on its WAN VLAN (iperf3 server, DHCP server
for the DUT's WAN port, NAT uplink to the internet), so all test traffic
passes through the DUT.

- The probe is its own labgrid place. The `probe` fixture (probe_fixtures.py)
  reserves and locks it for this module.
- The DUT's LAN VLAN comes from its NetworkService (192.168.1.1%vlan<N>).
- The DUT's WAN VLAN comes from the place's "wan" NetworkInterface (exporter.yaml).

Places without a VLAN (QEMU) and labs without a probe are skipped.
"""

# ruff: noqa: F811 (tests take the imported probe fixtures as arguments)

import ipaddress
import json
import logging
import os
import time

import pytest
from probe_fixtures import (
    DUT_LAN,
    DUT_LAN_IP,
    lease,  # noqa: F401 (fixture)
    online,  # noqa: F401 (fixture)
    slot,  # noqa: F401 (fixture)
    wan_slot,  # noqa: F401 (fixture)
)

logger = logging.getLogger(__name__)

IPERF_SECONDS = 10
# Only catches a broken forwarding path; the measured numbers go to record_property.
MIN_MBPS = 20
# LAN soak against the DUT's own iperf3 server; longer runs: PROBE_SOAK_SECONDS
SOAK_SECONDS = int(os.environ.get("PROBE_SOAK_SECONDS", "60"))
# UDP through the DUT's NAT at a fixed rate
UDP_BITRATE = "50M"
UDP_MAX_LOSS_PERCENT = 5.0
DUT_IPERF_PORT = 5201


# --- tests --------------------------------------------------------------------


def test_lan_dhcp_lease(lease, record_property):
    """The DUT's DHCP server gives a LAN client an address in the LAN subnet,
    with the DUT as router and DNS server. Without this, computers plugged
    into the LAN get no working network configuration."""
    ip = ipaddress.ip_address(lease["ip"])
    record_property("probe_lan_ip", str(ip))

    assert ip in DUT_LAN, f"lease {ip} not in {DUT_LAN}: {lease}"
    assert str(ip) != DUT_LAN_IP
    assert lease["router"][:1] == [DUT_LAN_IP], lease
    assert DUT_LAN_IP in lease["dns"], lease
    assert lease["lease"] > 0, lease


def test_lan_lease_on_dut(shell_command, slot, lease):
    """The DUT's lease table (dnsmasq) lists the LAN client's lease with its
    MAC address and hostname. LuCI's client overview and local name
    resolution read this table."""
    mac = slot.info["lan_mac"].lower()
    leases = shell_command.run("cat /tmp/dhcp.leases")[0]
    entries = [line.split() for line in leases if mac in line.lower()]
    assert entries, f"no lease for {mac} in /tmp/dhcp.leases:\n" + "\n".join(leases)

    _, _, ip, hostname, *_ = entries[0]
    assert ip == lease["ip"]
    assert hostname == slot.info["hostname"]


def test_lan_ping_dut(slot, lease):
    """A LAN client with a DHCP lease can ping the DUT, so the DUT is
    reachable from the LAN."""
    reply = slot.call("ping", host=DUT_LAN_IP, count=3)
    assert reply["received"] > 0, reply["out"]


def test_lan_dns_dhcp_hostname(slot, lease):
    """The DUT's DNS resolves a LAN client by the hostname it sent in its
    DHCP request (e.g. `<host>.lan`), so devices on the LAN can reach each
    other by name."""
    name = f"{slot.info['hostname']}.{lease.get('domain') or 'lan'}"
    reply = slot.call("resolve", name=name, server=DUT_LAN_IP)
    assert lease["ip"] in reply["addresses"], (
        f"{name} resolved to {reply['addresses']}:\n{reply['out']}"
    )


def test_lan_ipv6_slaac(slot, lease, record_property):
    """A LAN client gets a global-scope IPv6 address (the DUT's ULA prefix)
    from the DUT's router advertisements within 30 seconds. Without it, IPv6
    on the LAN doesn't work."""
    addresses = []
    deadline = time.monotonic() + 30
    while not addresses and time.monotonic() < deadline:
        addresses = [
            f"{a['address']}/{a['prefixlen']}"
            for a in slot.call("addresses")["ipv6"]
            if a["scope"] == "global"
        ]
        if not addresses:
            time.sleep(3)

    record_property("probe_lan_ipv6", json.dumps(addresses))
    assert addresses, f"no global IPv6 address on {slot.info['lan_dev']} after 30s"


@pytest.mark.lg_feature("wan_port")
def test_wan_reachable(wan_slot, lease):
    """A LAN client reaches a server on the DUT's WAN side through the DUT's
    NAT. That's the basic job of a router: LAN devices get out to the
    upstream network."""
    server = wan_slot.info["wan_ip"]
    reply = wan_slot.call("ping", host=server, count=3)
    assert reply["received"] > 0, (
        f"{server} on vlan {wan_slot.info['wan_vlan']} unreachable: "
        f"is the probe's switch port tagged for it?\n{reply['out']}"
    )


@pytest.mark.lg_feature("wan_port")
@pytest.mark.parametrize("direction", ["upload", "download"])
def test_speedtest(wan_slot, lease, direction, record_property):
    """TCP throughput through the DUT's NAT, LAN→WAN (upload) and WAN→LAN
    (download), one run per direction. It records the speed and fails only
    below 20 Mbit/s, which means the forwarding path is broken rather than
    slow."""
    result = wan_slot.speedtest(duration=IPERF_SECONDS, reverse=direction == "download")
    assert result["rc"] == 0, result

    mbps = result["received_bps"] / 1e6
    logger.info(
        "speedtest %s: %.1f Mbit/s, %s retransmits",
        direction,
        mbps,
        result.get("retransmits"),
    )
    record_property(f"probe_{direction}_mbps", round(mbps, 1))
    record_property(f"probe_{direction}_retransmits", result.get("retransmits"))

    assert mbps >= MIN_MBPS, f"{direction}: {mbps:.1f} Mbit/s < {MIN_MBPS}"


@pytest.mark.lg_feature("wan_port")
@pytest.mark.parametrize("direction", ["upload", "download"])
def test_speedtest_udp(wan_slot, lease, direction, record_property):
    """UDP at 50 Mbit/s through the DUT's NAT, one run per direction (upload
    and download). It records throughput, loss and jitter, and fails above 5%
    packet loss. Loss like that hurts voice, video and games."""
    result = wan_slot.speedtest(
        duration=IPERF_SECONDS,
        reverse=direction == "download",
        udp=True,
        bitrate=UDP_BITRATE,
    )
    assert result["rc"] == 0, result
    assert result["udp"], result

    loss = result.get("lost_percent")
    record_property(
        f"probe_udp_{direction}_mbps", round(result["received_bps"] / 1e6, 1)
    )
    record_property(f"probe_udp_{direction}_lost_percent", loss)
    record_property(f"probe_udp_{direction}_jitter_ms", result.get("jitter_ms"))

    assert result.get("packets"), f"no UDP packets counted: {result}"
    assert loss is not None and loss <= UDP_MAX_LOSS_PERCENT, (
        f"{direction}: {loss}% of UDP packets lost at {UDP_BITRATE}bit/s: {result}"
    )


@pytest.fixture
def dut_iperf_server(request, shell_command):
    """iperf3 -s on the DUT. If the image lacks it, it's installed over the
    DUT's WAN (online fixture, so that needs a cabled WAN port)."""
    if shell_command.run("command -v iperf3")[2] != 0:
        request.getfixturevalue("online")
        shell_command.run_check("apk update && apk add iperf3", timeout=300)
    shell_command.run("killall iperf3")
    shell_command.run_check(f"iperf3 -s -D -p {DUT_IPERF_PORT}")
    time.sleep(1)
    yield DUT_LAN_IP
    shell_command.run("killall iperf3")


def tx_timeouts(shell):
    return [
        line
        for line in shell.run("dmesg")[0]
        if "NETDEV WATCHDOG" in line or "transmit queue" in line
    ]


@pytest.mark.parametrize("direction", ["upload", "download"])
def test_lan_soak_no_tx_timeout(
    slot, lease, dut_iperf_server, shell_command, direction, record_property
):
    """Sustained LAN traffic to and from the DUT (one run per direction)
    causes no network driver transmit timeouts (NETDEV WATCHDOG). Such
    timeouts can stall or reset the Ethernet port under load.

    Regression: openwrt/openwrt#20570

    TCP between the probe's LAN client and the DUT's own iperf3 server (no
    WAN port needed if the image has iperf3), then no NETDEV WATCHDOG tx
    timeout in dmesg.
    PROBE_SOAK_SECONDS sets the duration (default 60 s).
    """
    before = tx_timeouts(shell_command)
    result = slot.speedtest(
        duration=SOAK_SECONDS,
        reverse=direction == "download",
        server=dut_iperf_server,
        port=DUT_IPERF_PORT,
        streams=4,
    )
    assert result["rc"] == 0, result

    mbps = result["received_bps"] / 1e6
    record_property(f"probe_lan_dut_{direction}_mbps", round(mbps, 1))
    record_property(f"probe_lan_dut_{direction}_retransmits", result.get("retransmits"))

    new = [line for line in tx_timeouts(shell_command) if line not in before]
    assert not new, "tx timeout during LAN traffic:\n" + "\n".join(new)
    assert mbps >= MIN_MBPS, f"{direction}: {mbps:.1f} Mbit/s < {MIN_MBPS}"
