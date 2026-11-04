"""
LAN-side tests through the labgrid-probe (contrib/labgrid-probe).

The probe puts one network namespace on the DUT's LAN VLAN (DHCP/DNS/SLAAC
client, iperf3 client) and one on its WAN VLAN (iperf3 server, DHCP server
for the DUT's WAN port), so all test traffic passes through the DUT.

- The probe is its own labgrid place. The `probe` fixture (probe_fixtures.py)
  reserves and locks it for this module.
- The DUT's LAN VLAN comes from its NetworkService (192.168.1.1%vlan<N>).
- The DUT's WAN VLAN comes from the place's "wan" NetworkInterface (exporter.yaml).

Places without a VLAN (QEMU) and labs without a probe are skipped.
"""

import ipaddress
import json
import logging
import re
import time

import pytest

from labgrid.exceptions import NoResourceFoundError

logger = logging.getLogger(__name__)

DUT_LAN_IP = "192.168.1.1"
DUT_LAN = ipaddress.ip_network("192.168.1.0/24")

IPERF_SECONDS = 10
# Only catches a broken forwarding path; the measured numbers go to record_property.
MIN_MBPS = 20


# --- DUT side -----------------------------------------------------------------


def interface_status(shell, iface):
    """`ubus call network.interface.<iface> status`, or None while it doesn't
    exist yet (netifd still starting: "Command failed: Not found")."""
    stdout, _, rc = shell.run(f"ubus call network.interface.{iface} status")
    if rc != 0:
        return None
    try:
        return json.loads("\n".join(stdout))
    except json.JSONDecodeError:
        return None


def wait_for_interface(shell, iface, timeout=120, need_ipv4=False):
    """Wait until a DUT interface is up (with an IPv4 address), return its
    status. labgrid reaches `shell` once the console offers a login, which on
    slow boards (ath79) is well before netifd has set up the network."""
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        status = interface_status(shell, iface)
        if (
            status
            and status.get("up")
            and (not need_ipv4 or status.get("ipv4-address"))
        ):
            return status
        time.sleep(3)
    pytest.fail(
        f"DUT {iface} not up after {timeout}s: {status or 'interface not found'}"
    )


def wait_for_dhcp_server(shell, timeout=120):
    """Wait until the DUT's LAN is up and its dnsmasq runs."""
    wait_for_interface(shell, "lan", timeout)
    deadline = time.monotonic() + timeout
    while shell.run("pidof dnsmasq")[2] != 0:
        if time.monotonic() > deadline:
            pytest.fail(f"dnsmasq not running on the DUT after {timeout}s")
        time.sleep(3)


def lan_vlan(target):
    """The DUT's LAN VLAN, from its NetworkService (192.168.1.1%vlan<N>)."""
    address = target.get_resource("NetworkService").address
    match = re.search(r"%vlan(\d+)$", address)
    if not match:
        pytest.skip(f"NetworkService {address} isn't on a lab VLAN")
    return int(match.group(1))


def wan_vlan(target):
    """The DUT's WAN VLAN, from the place's "wan" NetworkInterface (arrives as
    RemoteNetworkInterface through the RemotePlace)."""
    try:
        iface = target.get_resource("RemoteNetworkInterface", name="wan")
    except NoResourceFoundError:
        pytest.skip("place has no 'wan' NetworkInterface: WAN port not cabled")
    match = re.fullmatch(r"vlan(\d+)", iface.ifname)
    if not match:
        pytest.fail(f"'wan' interface {iface.ifname} isn't a lab VLAN (vlan<N>)")
    return int(match.group(1))


# --- fixtures -----------------------------------------------------------------


class Slot:
    """This DUT's slot on the probe: probe calls with the DUT's LAN VLAN."""

    def __init__(self, probe, vlan):
        self.probe = probe
        self.vlan = vlan
        self.info = {}

    def call(self, method, **params):
        return self.probe.call(method, lan_vlan=self.vlan, **params)


# The probe is shared by the whole lab, so it's reserved last: only once the
# DUT is known to have the VLANs the tests need and has booted. The fixtures
# fetch it with getfixturevalue() rather than as an argument, because pytest
# would otherwise reserve it (possibly waiting in the queue) before these
# checks can skip.


@pytest.fixture(scope="module")
def slot(request, strategy, target):
    vlan = lan_vlan(target)
    strategy.transition("shell")
    probe = request.getfixturevalue("probe")
    slot = Slot(probe, vlan)
    slot.info = slot.call("up")
    yield slot
    probe.call_raw("down", lan_vlan=slot.vlan)


@pytest.fixture(scope="module")
def wan_slot(request, strategy, target):
    """The slot with the WAN side up: iperf3 server and the DUT's WAN lease."""
    vlan = wan_vlan(target)
    slot = request.getfixturevalue("slot")
    slot.info = slot.call("wan_up", wan_vlan=vlan)
    wait_for_interface(strategy.shell, "wan", need_ipv4=True)
    return slot


@pytest.fixture(scope="module")
def lease(strategy, slot):
    wait_for_dhcp_server(strategy.shell)
    lease = slot.call("dhcp")["lease"]
    # Everything else depends on the lease coming from the DUT. The lab host
    # serves no DHCP (ansible/files/exporter/dnsmasq.conf); another server here
    # means a lab-specific dnsmasq.conf still serving the DUT LAN VLANs.
    if lease.get("serverid") != DUT_LAN_IP:
        pytest.fail(
            f"DHCP lease {lease.get('ip')} came from {lease.get('serverid')}, "
            f"not the DUT ({DUT_LAN_IP}). Is another DHCP server (the lab "
            "host's dnsmasq?) serving the DUT's LAN VLAN?"
        )
    return lease


# --- tests --------------------------------------------------------------------


def test_lan_dhcp_lease(lease, record_property):
    """The DUT hands a LAN client a lease with itself as router and DNS."""
    ip = ipaddress.ip_address(lease["ip"])
    record_property("probe_lan_ip", str(ip))

    assert ip in DUT_LAN, f"lease {ip} not in {DUT_LAN}: {lease}"
    assert str(ip) != DUT_LAN_IP
    assert lease["router"][:1] == [DUT_LAN_IP], lease
    assert DUT_LAN_IP in lease["dns"], lease
    assert lease["lease"] > 0, lease


def test_lan_lease_on_dut(shell_command, slot, lease):
    """The DUT's dnsmasq lists the lease with the client's MAC and hostname."""
    mac = slot.info["lan_mac"].lower()
    leases = shell_command.run("cat /tmp/dhcp.leases")[0]
    entries = [line.split() for line in leases if mac in line.lower()]
    assert entries, f"no lease for {mac} in /tmp/dhcp.leases:\n" + "\n".join(leases)

    _, _, ip, hostname, *_ = entries[0]
    assert ip == lease["ip"]
    assert hostname == slot.info["hostname"]


def test_lan_ping_dut(slot, lease):
    reply = slot.call("ping", host=DUT_LAN_IP, count=3)
    assert reply["received"] > 0, reply["out"]


def test_lan_dns_dhcp_hostname(slot, lease):
    """dnsmasq resolves the hostname the client sent in its DHCP request."""
    name = f"{slot.info['hostname']}.{lease.get('domain') or 'lan'}"
    reply = slot.call("resolve", name=name, server=DUT_LAN_IP)
    assert lease["ip"] in reply["addresses"], (
        f"{name} resolved to {reply['addresses']}:\n{reply['out']}"
    )


def test_lan_ipv6_slaac(slot, lease, record_property):
    """The DUT's router advertisements give the client a (ULA) address."""
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
    """The probe's WAN-side server answers through the DUT's NAT."""
    server = wan_slot.info["wan_ip"]
    reply = wan_slot.call("ping", host=server, count=3)
    assert reply["received"] > 0, (
        f"{server} on vlan {wan_slot.info['wan_vlan']} unreachable: "
        f"is the probe's switch port tagged for it?\n{reply['out']}"
    )


@pytest.mark.lg_feature("wan_port")
@pytest.mark.parametrize("direction", ["upload", "download"])
def test_speedtest(wan_slot, lease, direction, record_property):
    """TCP throughput LAN→WAN (upload) and WAN→LAN (download) through the DUT."""
    wan_slot.call(
        "speedtest_start", duration=IPERF_SECONDS, reverse=direction == "download"
    )
    deadline = time.monotonic() + IPERF_SECONDS + 60
    result = {"running": True}
    while result.get("running") and time.monotonic() < deadline:
        time.sleep(2)
        result = wan_slot.call("speedtest_result")
    assert not result.get("running"), "speedtest did not finish"
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
