"""The `probe` fixture: the lab's labgrid-probe, reserved and locked for the
test module that uses it.

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
"""

import asyncio
import logging
import sys
import time
from pathlib import Path

import grpc
import pytest

from labgrid.factory import target_factory
from labgrid.remote.common import Reservation, ReservationState
from labgrid.remote.generated import labgrid_coordinator_pb2 as pb2
from labgrid.resource.remote import RemotePlaceManager

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drivers.labgridprobedriver import LabgridProbeDriver  # noqa: F401 (registers it)

logger = logging.getLogger(__name__)

PROBE_TAGS = {"role": "probe"}
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

    def cancel(self, token):
        self.call("CancelReservation", pb2.CancelReservationRequest(token=token))


@pytest.fixture(scope="module")
def probe(env, target):
    """The probe's LabgridProbeDriver, locked for the requesting module.

    Skips without a coordinator (QEMU) or if the lab has no probe (no place
    tagged role=probe). Depends on `target` so the DUT's RemotePlace has
    opened the coordinator session.
    """
    manager = RemotePlaceManager.get()
    if manager.session is None:
        pytest.skip("no labgrid coordinator session (not a remote place)")
    coordinator = Coordinator(manager)

    if not coordinator.places_tagged(PROBE_TAGS):
        pytest.skip(f"no place tagged {PROBE_TAGS} on this coordinator")

    res = coordinator.reserve(PROBE_TAGS)
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

        probe_target = target_factory.make_target(
            "probe",
            {
                "resources": {"RemotePlace": {"name": place}},
                "drivers": {"LabgridProbeDriver": {}},
            },
            env=env,
        )
        try:
            yield probe_target.get_driver("LabgridProbeDriver")
        finally:
            probe_target.cleanup()
    finally:
        if place:
            try:
                coordinator.release(place)
            except RuntimeError as e:
                logger.warning("releasing %s failed: %s", place, e)
        try:
            coordinator.cancel(res.id)
        except RuntimeError as e:
            logger.warning("cancelling reservation %s failed: %s", res.id, e)
