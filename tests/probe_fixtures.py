"""Fixtures for the lab's labgrid-probe (contrib/labgrid-probe).

`probe`: the probe, reserved and locked for the test module that uses it.
The probe is a labgrid place of its own (labgrid-<lab>-probe, tag
role=probe; labnet.yaml `probe: true`). The fixture reserves any place with
that tag through the coordinator, waits until it's allocated, acquires it, and
holds it for the module: other DUTs' probe tests queue at the coordinator
(`labgrid-client reservations`/`who`). With several probes later, the
coordinator hands out a free one.

It talks to the coordinator over the session the DUT's RemotePlace already
opened (RemotePlaceManager), the same calls `labgrid-client reserve/wait/
lock/unlock/cancel-reservation` make. Acquiring through that session also
keeps its cached place state current, so the probe's RemotePlace sees the
lock right away.

The probe is released at module teardown, which also runs on Ctrl-C and
SIGTERM (conftest.py). While held, the probe place's comment names the DUT
place (PROBE_HOLDER), so the KernelCI agent can release it if pytest is
killed outright.

On top of it (module scope; conftest.py only imports `probe`, so a test
module imports the ones it uses, including those they depend on):

    from probe_fixtures import lease, online, slot, wan_slot  # noqa: F401

- `slot`: this DUT's slot on the probe (a LAN client namespace). Skips on
  places without a LAN VLAN (QEMU) and in labs without a probe.
- `wan_slot`: `slot` with the WAN side up (iperf3 server, the DUT's WAN DHCP
  server and internet uplink: the probe NATs it to the lab network); waits
  for the DUT's WAN lease. Skips without a cabled WAN port.
- `lease`: the probe's DHCPv4 lease from the DUT's LAN.
- `online`: the DUT is online through its WAN (needs `wan_slot`). Tests
  request it next to `ssh_command` and run apk, uclient-fetch, ... there.
"""

import asyncio
import datetime
import ipaddress
import json
import logging
import re
import sys
import time
from pathlib import Path

import grpc
import pytest
from labgrid.exceptions import NoResourceFoundError
from labgrid.factory import target_factory
from labgrid.remote.common import Reservation, ReservationState
from labgrid.remote.generated import labgrid_coordinator_pb2 as pb2
from labgrid.resource.remote import RemotePlaceManager

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drivers.labgridprobedriver import (  # noqa: F401 (registers the driver)
    LabgridProbeDriver,
    ProbeError,
)

logger = logging.getLogger(__name__)

PROBE_TAGS = {"role": "probe"}
# Comment on the probe place while a test holds it, naming the DUT place.
# pytest releases the probe at teardown; if it is killed first (SIGKILL), the
# KernelCI agent (kernelci-labgrid Coordinator.release) releases probes whose
# comment names the DUT place it is giving up. Keep both in sync.
PROBE_HOLDER = "held for {}"
# How long a test module waits for a probe another module holds
PROBE_WAIT_TIMEOUT = 1800
RPC_TIMEOUT = 30


class Coordinator:
    """Blocking wrappers around the shared coordinator session."""

    def __init__(self, manager):
        self.loop = manager.loop
        self.session = manager.session

    def _run(self, coro, timeout=RPC_TIMEOUT):
        return self.loop.run_until_complete(asyncio.wait_for(coro, timeout))

    def call(self, rpc, request):
        try:
            return self._run(getattr(self.session.stub, rpc)(request))
        except grpc.aio.AioRpcError as e:
            raise RuntimeError(f"{rpc}: {e.details()}") from e

    def sync(self):
        self._run(self.session.sync_with_coordinator())

    def places_tagged(self, tags):
        self.sync()
        return [
            name
            for name, place in self.session.places.items()
            if all(place.tags.get(k) == v for k, v in tags.items())
        ]

    def reserve(self, tags):
        request = pb2.CreateReservationRequest(
            filters={"main": pb2.Reservation.Filter(filter=tags)}, prio=0.0
        )
        response = self.call("CreateReservation", request)
        return Reservation.from_pb2(response.reservation)

    def wait(self, token, timeout):
        deadline = time.monotonic() + timeout
        while True:
            response = self.call(
                "PollReservation", pb2.PollReservationRequest(token=token)
            )
            res = Reservation.from_pb2(response.reservation)
            if res.state is not ReservationState.waiting:
                return res
            if time.monotonic() > deadline:
                return res
            self._run(asyncio.sleep(1.0))

    def acquire(self, place):
        self.call("AcquirePlace", pb2.AcquirePlaceRequest(placename=place))
        self.sync()

    def release(self, place):
        self.call("ReleasePlace", pb2.ReleasePlaceRequest(placename=place))
        self.sync()

    def set_comment(self, place, comment):
        self.call(
            "SetPlaceComment",
            pb2.SetPlaceCommentRequest(placename=place, comment=comment),
        )

    def cancel(self, token):
        self.call("CancelReservation", pb2.CancelReservationRequest(token=token))


def hold_probe_place(env, target, tags, driver, name):
    """Generator for a module fixture: reserve and lock a place with `tags`,
    yield `driver` of a target `name` on it, release it afterwards.

    Skips without a coordinator (QEMU) or if no place has `tags`. `target`
    (the DUT's) has opened the coordinator session through its RemotePlace.
    Used by `probe` and by wifi_probe_fixtures.py's `wifi_probe`.
    """
    manager = RemotePlaceManager.get()
    if manager.session is None:
        pytest.skip("no labgrid coordinator session (not a remote place)")
    coordinator = Coordinator(manager)

    if not coordinator.places_tagged(tags):
        pytest.skip(f"no place tagged {tags} on this coordinator")

    res = coordinator.reserve(tags)
    place = None
    try:
        res = coordinator.wait(res.id, PROBE_WAIT_TIMEOUT)
        if res.state is ReservationState.waiting:
            pytest.fail(f"no probe free within {PROBE_WAIT_TIMEOUT}s")
        if res.state is not ReservationState.allocated:
            pytest.fail(f"probe reservation {res.id} is {res.state.name}")
        allocated = res.allocations["main"][0]
        coordinator.acquire(allocated)
        place = allocated
        logger.info("acquired probe place %s (reservation %s)", place, res.id)
        dut_place = target.get_resource("RemotePlace").name
        coordinator.set_comment(place, PROBE_HOLDER.format(dut_place))

        probe_target = target_factory.make_target(
            name,
            {
                "resources": {"RemotePlace": {"name": place}},
                "drivers": {driver: {}},
            },
            env=env,
        )
        try:
            yield probe_target.get_driver(driver)
        finally:
            probe_target.cleanup()
    finally:
        if place:
            try:
                coordinator.set_comment(place, "")
            except RuntimeError as e:
                logger.warning("clearing the comment of %s failed: %s", place, e)
            try:
                coordinator.release(place)
            except RuntimeError as e:
                logger.warning("releasing %s failed: %s", place, e)
        try:
            coordinator.cancel(res.id)
        except RuntimeError as e:
            logger.warning("cancelling reservation %s failed: %s", res.id, e)


@pytest.fixture(scope="module")
def probe(env, target):
    """The probe's LabgridProbeDriver, locked for the requesting module.

    Skips without a coordinator (QEMU) or if the lab has no probe (no place
    tagged role=probe). Depends on `target` so the DUT's RemotePlace has
    opened the coordinator session.
    """
    yield from hold_probe_place(env, target, PROBE_TAGS, "LabgridProbeDriver", "probe")


# --- DUT side -----------------------------------------------------------------

DUT_LAN_IP = "192.168.1.1"
DUT_LAN = ipaddress.ip_network("192.168.1.0/24")


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


def lan_link(target):
    """The lab host's interface towards the DUT's LAN and its VLAN (None if
    it has none), from the DUT's NetworkService: 192.168.1.1%vlan<N>, or
    %eth1 for a DUT cabled straight to the lab host. A probe of its own
    needs the VLAN; one on the lab host (local mode) takes the interface."""
    address = target.get_resource("NetworkService").address
    match = re.search(r"%([\w.-]+)$", address)
    if not match:
        pytest.skip(f"NetworkService {address} names no lab host interface")
    dev = match.group(1)
    vlan = re.fullmatch(r"vlan(\d+)", dev)
    return dev, int(vlan.group(1)) if vlan else None


def wan_link(target):
    """The lab host's interface towards the DUT's WAN and its VLAN (None if
    it has none), from the place's "wan" NetworkInterface (arrives as
    RemoteNetworkInterface through the RemotePlace): vlan<N>, or a port the
    DUT's WAN is cabled to straight (lab host probe only)."""
    try:
        iface = target.get_resource("RemoteNetworkInterface", name="wan")
    except NoResourceFoundError:
        pytest.skip("place has no 'wan' NetworkInterface: WAN port not cabled")
    match = re.fullmatch(r"vlan(\d+)", iface.ifname)
    return iface.ifname, int(match.group(1)) if match else None


def sync_clock(shell, tolerance=60):
    """Set the DUT's clock from this host's if it is off by more than
    `tolerance` s. A DUT boots with its build date, which TLS certificate
    checks reject until sysntpd has synced. `shell`: any labgrid command
    driver (ShellDriver, SSHDriver)."""
    dut = int(shell.run_check("date +%s")[-1].strip())
    now = time.time()
    if abs(dut - now) <= tolerance:
        return
    stamp = datetime.datetime.fromtimestamp(now, datetime.UTC)
    shell.run_check(f"date -u -s '{stamp:%Y-%m-%d %H:%M:%S}'")
    logger.info("DUT clock was off by %ds, set to %s UTC", now - dut, stamp)


# --- probe slot -----------------------------------------------------------------

# Driver helpers that take the LAN VLAN first; Slot binds it.
SLOT_METHODS = {
    "wan_dhcp",
    "speedtest_start",
    "speedtest_result",
    "speedtest",
    "ra_capture",
    "mcast_join",
    "mcast_send",
    "mcast_result",
    "mcast_leave",
}


class Slot:
    """This DUT's slot on the probe: probe calls with the DUT's LAN VLAN.

    `slot.call("ping", host=...)` for any ubus method, and the driver helpers
    with the VLAN bound: `slot.ra_capture(timeout=5)`, `slot.speedtest(...)`.
    """

    def __init__(self, probe, vlan):
        self.probe = probe
        self.vlan = vlan
        self.info = {}

    def call(self, method, **params):
        return self.probe.call(method, lan_vlan=self.vlan, **params)

    def __getattr__(self, name):
        if name not in SLOT_METHODS:
            raise AttributeError(name)
        method = getattr(self.probe, name)

        def bound(*args, **kwargs):
            return method(self.vlan, *args, **kwargs)

        return bound


def _open_slot(request, strategy, target):
    dev, vlan = lan_link(target)
    strategy.transition("shell")
    probe = request.getfixturevalue("probe")
    params = {"parent": dev} if vlan is None else {"lan_vlan": vlan, "parent": dev}
    status, info = probe.call_raw("up", **params)
    if status == 0 and info and info.get("needs_vlan"):
        pytest.skip(f"the lab's probe needs a DUT VLAN, {dev} has none")
    if status != 0 or not info or "error" in info:
        raise ProbeError(f"labgrid-probe.up: {info or f'ubus status {status}'}")
    # The slot id: the VLAN, or the one a lab host probe picked for dev
    slot = Slot(probe, info["lan_vlan"])
    slot.info = info
    return slot


def _get_lease(strategy, slot):
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


# The probe is shared by the whole lab, so it's reserved last: only once the
# DUT is known to have the VLANs the tests need and has booted. The fixtures
# fetch it with getfixturevalue() rather than as an argument, because pytest
# would otherwise reserve it (possibly waiting in the queue) before these
# checks can skip.


@pytest.fixture(scope="module")
def slot(request, strategy, target):
    slot = _open_slot(request, strategy, target)
    yield slot
    slot.probe.call_raw("down", lan_vlan=slot.vlan)


@pytest.fixture(scope="module")
def wan_slot(request, strategy, target):
    """The slot with the WAN side up: iperf3 server and the DUT's WAN lease,
    with the probe as router to the internet."""
    dev, vlan = wan_link(target)
    slot = request.getfixturevalue("slot")
    params = (
        {"wan_parent": dev} if vlan is None else {"wan_vlan": vlan, "wan_parent": dev}
    )
    slot.info = slot.call("wan_up", **params)
    # A lease from an earlier WAN DHCP server would keep its router and DNS
    # servers until it renews: request a fresh one
    strategy.shell.run_check("ifup wan")
    time.sleep(3)
    wait_for_interface(strategy.shell, "wan", need_ipv4=True)
    return slot


@pytest.fixture(scope="module")
def lease(strategy, slot):
    return _get_lease(strategy, slot)


# --- internet through the DUT's WAN ---------------------------------------------------

ONLINE_CHECK_HOST = "downloads.openwrt.org"


def wait_online(command, timeout=60):
    """Wait until the DUT resolves ONLINE_CHECK_HOST (WAN lease, NAT and DNS
    work), then fix its clock for TLS. Call it again after a reboot."""
    deadline = time.monotonic() + timeout
    out = []
    while time.monotonic() < deadline:
        out, _, rc = command.run(f"nslookup {ONLINE_CHECK_HOST}", timeout=30)
        if rc == 0:
            break
        time.sleep(3)
    else:
        # No upstream DNS: the WAN lease had none (old probe code?); no route
        # or unreachable servers: the probe's NAT
        state, _, _ = command.run(
            "echo '# resolv.conf.auto:'; cat /tmp/resolv.conf.d/resolv.conf.auto; "
            "echo '# default route:'; ip route show default; "
            "echo '# ping 1.1.1.1:'; ping -c 2 -W 2 1.1.1.1"
        )
        pytest.fail(
            f"DUT can't resolve {ONLINE_CHECK_HOST} through its WAN after "
            f"{timeout}s:\n" + "\n".join(out + state)
        )
    sync_clock(command)


@pytest.fixture(scope="module")
def online(wan_slot, target):
    """The DUT is online through its WAN port.

    The probe is the DUT's WAN router and NATs to the internet (labgrid-probe
    wan_up), so nothing needs configuring on the DUT: request this next to
    `ssh_command` and run apk, uclient-fetch etc. directly. Needs `slot` and
    `wan_slot` imported too; skips where they skip (QEMU, labs without a
    probe, no cabled WAN port).
    """
    wait_online(target.get_driver("SSHDriver"))
