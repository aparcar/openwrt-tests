"""Fixtures for the Debian lab probe (contrib/debian-probe, docs/debian-probe.md).

The probe is a labgrid place of its own, tagged role=debian-probe, whose only
resource is a NetworkService (the probe's SSH). Unlike the OpenWrt
labgrid-probe it is *not* locked: every DUT gets its own slot on the probe
(namespaces keyed by the DUT's LAN VLAN), and the DUT's own place lock
already makes that slot exclusive. So any number of DUTs use one probe at
the same time. The fixture reads the NetworkService straight from the
coordinator session the DUT's RemotePlace opened and builds a target with
SSHDriver + DebianProbeDriver on it.

conftest.py imports the fixtures (dprobe, dprobe_slot, dprobe_wan,
dprobe_lan_lease); test modules import the helpers they use from here.
"""

import contextlib
import json
import logging
import re
import sys
import time
from pathlib import Path

import pytest
from labgrid.exceptions import NoResourceFoundError
from labgrid.factory import target_factory
from labgrid.resource.remote import RemotePlaceManager

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drivers.debianprobedriver import (
    DebianProbeDriver,  # noqa: F401 (registers it)
    DebianProbeError,
)

logger = logging.getLogger(__name__)

DPROBE_TAGS = {"role": "debian-probe"}
RPC_TIMEOUT = 30

# What dprobe puts on the DUT's WAN segment (dprobe/render.py WanPlan)
WAN_NET4 = "172.16.100.0/24"
WAN_SERVER4 = "172.16.100.1"
WAN_NET6 = "2001:db8:100::/64"
WAN_SERVER6 = "2001:db8:100::1"
PD_POOL = "2001:db8:200::/48"
DUT_LAN_IP = "192.168.1.1"


# --- finding the probe ---------------------------------------------------------


def _probe_service(session, tags):
    """(place name, NetworkService ResourceEntry) of a place with `tags`, or
    (None, None). Reads the coordinator's resource table: no lock needed."""
    import asyncio

    session.loop.run_until_complete(
        asyncio.wait_for(session.sync_with_coordinator(), RPC_TIMEOUT)
    )
    for name, place in sorted(session.places.items()):
        if not all(place.tags.get(k) == v for k, v in tags.items()):
            continue
        for exporter, groups in session.resources.items():
            for group, resources in groups.items():
                for rname, entry in resources.items():
                    path = (exporter, group, entry.cls, rname)
                    if entry.cls == "NetworkService" and place.hasmatch(path):
                        if entry.avail:
                            return name, entry
                        logger.warning(
                            "%s: NetworkService %s not available", name, path
                        )
    return None, None


@pytest.fixture(scope="module")
def dprobe(env, target):
    """DebianProbeDriver of the lab's Debian probe (shared, not locked).

    Skips without a coordinator (QEMU) or without a role=debian-probe place.
    Depends on `target` so the DUT's RemotePlace opened the session.
    """
    manager = RemotePlaceManager.get()
    if manager.session is None:
        pytest.skip("no labgrid coordinator session (not a remote place)")
    place, entry = _probe_service(manager.session, DPROBE_TAGS)
    if entry is None:
        pytest.skip(f"no place tagged {DPROBE_TAGS} with a NetworkService")

    params = entry.args
    probe_target = target_factory.make_target(
        "debian-probe",
        {
            "resources": {
                "NetworkService": {
                    "address": params["address"],
                    "username": params.get("username", "dprobe"),
                    "port": int(params.get("port", 22)),
                }
            },
            "drivers": {"SSHDriver": {}, "DebianProbeDriver": {}},
        },
        env=env,
    )
    # exporter-side proxy settings (proxy_required with --isolated exporters)
    probe_target.get_resource("NetworkService").extra = entry.extra
    logger.info("using Debian probe %s (%s)", place, params["address"])
    try:
        driver = probe_target.get_driver("DebianProbeDriver")
        yield driver
    finally:
        probe_target.cleanup()


# --- the DUT's VLANs -----------------------------------------------------------


def lan_vlan(target):
    """The DUT's LAN VLAN, from its NetworkService (192.168.1.1%vlan<N>)."""
    address = target.get_resource("NetworkService").address
    match = re.search(r"%vlan(\d+)$", address)
    if not match:
        pytest.skip(f"NetworkService {address} isn't on a lab VLAN")
    return int(match.group(1))


def wan_vlan(target):
    """The DUT's WAN VLAN from the place's "wan" NetworkInterface, or None
    (none, or a port of the lab host the DUT's WAN is cabled to straight:
    only a labgrid-probe on the lab host reaches that, see probe_fixtures)."""
    try:
        iface = target.get_resource("RemoteNetworkInterface", name="wan")
    except NoResourceFoundError:
        return None
    match = re.fullmatch(r"vlan(\d+)", iface.ifname)
    return int(match.group(1)) if match else None


# --- the slot ------------------------------------------------------------------


class Slot:
    """This DUT's slot on the probe: dprobe calls with the LAN VLAN bound."""

    def __init__(self, probe, lan, wan=None):
        self.probe = probe
        self.lan_vlan = lan
        self.wan_vlan = wan
        self.info = {}

    def call(self, *args, **options):
        """dprobe <args[0]> [<args[1]>] <lan_vlan> ...: for the two-level
        commands ("wan", "dhcp6") the VLAN goes after the subcommand."""
        if args and args[0] in ("wan", "slot"):
            args = (*args[:2], self.lan_vlan, *args[2:])
        else:
            args = (args[0], self.lan_vlan, *args[1:])
        return self.probe.call(*args, **options)

    def check(self, *args, **options):
        result = self.call(*args, **options)
        if not result.ok:
            raise DebianProbeError(f"dprobe {args}: {result.get('error')}", result)
        return result

    def logs(self, service, lines=40):
        return self.call("logs", service, lines=lines).get("lines", [])

    def up(self):
        self.info = self.probe.check(
            "slot", "up", self.lan_vlan, wan_vlan=self.wan_vlan
        )
        return self.info

    def down(self):
        try:
            self.probe.call("slot", "down", self.lan_vlan)
        except DebianProbeError as e:
            logger.warning("slot down %s: %s", self.lan_vlan, e)


@pytest.fixture(scope="module")
def dprobe_slot(request, strategy, target):
    """This DUT's slot (LAN side, plus the WAN side if the place has a "wan"
    interface). The DUT is booted first; the probe is fetched only then, so
    QEMU / probe-less labs skip before anything is set up."""
    lan = lan_vlan(target)
    wan = wan_vlan(target)
    strategy.transition("shell")
    probe = request.getfixturevalue("dprobe")
    slot = Slot(probe, lan, wan)
    slot.up()
    yield slot
    slot.down()


@pytest.fixture(scope="module")
def dprobe_wan(dprobe_slot):
    """The slot, skipping unless the DUT's WAN port is on a probe VLAN."""
    if dprobe_slot.wan_vlan is None:
        pytest.skip("place has no 'wan' NetworkInterface: WAN port not cabled")
    return dprobe_slot


@pytest.fixture(scope="module")
def dprobe_lan_lease(strategy, dprobe_slot):
    """A DHCPv4 lease from the DUT, configured in the slot's LAN namespace."""
    wait_for_interface(strategy.shell, "lan")
    result = None
    for _ in range(3):
        result = dprobe_slot.call("dhcp4", server=DUT_LAN_IP, apply=True)
        if result.ok:
            return result["lease"]
        time.sleep(5)
    pytest.fail(f"no DHCPv4 lease from the DUT on the LAN: {result}")


# --- DUT side --------------------------------------------------------------------


def interface_status(shell, iface):
    """`ubus call network.interface.<iface> status`, or None."""
    stdout, _, rc = shell.run(f"ubus call network.interface.{iface} status")
    if rc != 0:
        return None
    try:
        return json.loads("\n".join(stdout))
    except json.JSONDecodeError:
        return None


def wait_for_interface(shell, iface, timeout=120, check=None):
    """Wait until `iface` is up and `check(status)` (if given) is true;
    return the status. Fails the test on timeout."""
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        status = interface_status(shell, iface)
        if status and status.get("up") and (check is None or check(status)):
            return status
        time.sleep(3)
    pytest.fail(f"DUT {iface} not ready after {timeout}s: {status or 'not found'}")


@contextlib.contextmanager
def dut_config(shell, *configs, reload="/etc/init.d/network reload"):
    """Back up /etc/config/<configs> on the DUT, restore and reload on exit."""
    for name in configs:
        shell.run_check(f"cp /etc/config/{name} /tmp/dprobe-{name}.bak")
    try:
        yield
    finally:
        for name in configs:
            shell.run(f"cp /tmp/dprobe-{name}.bak /etc/config/{name}")
        shell.run(reload)
        time.sleep(5)


def uci(shell, *commands):
    """Run `uci <command>` for each command, then commit."""
    for command in commands:
        shell.run_check(f"uci {command}")
    shell.run_check("uci commit")


def record_json(record_property, name, value):
    """record_property with JSON (JUnit properties are flat strings)."""
    record_property(name, json.dumps(value, default=str))
