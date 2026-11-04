"""labgrid driver for the labgrid-probe (contrib/labgrid-probe).

The probe exports a NetworkService for its HTTP endpoint (uhttpd's /ubus,
port 80). This driver calls the probe's "labgrid-probe" ubus object as
JSON-RPC with rpcd's unauthenticated session, which the probe's ACL limits to
exactly those methods. The connection goes through labgrid's proxy handling
(LG_PROXY / exporter proxy), like any other network resource.
"""

import json
import urllib.error
import urllib.request
from typing import ClassVar

import attr
from labgrid.driver.common import Driver
from labgrid.factory import target_factory
from labgrid.step import step
from labgrid.util.proxy import proxymanager
from labgrid.util.ssh import sshmanager


class ProbeError(Exception):
    """A probe call failed: transport, ubus status, or an `error` reply."""


@target_factory.reg_driver
@attr.s(eq=False)
class LabgridProbeDriver(Driver):
    """Call methods of the probe's `labgrid-probe` ubus object."""

    bindings: ClassVar[dict] = {"service": "NetworkService"}

    timeout = attr.ib(default=60.0, validator=attr.validators.instance_of(float))

    SESSION = "0" * 32  # rpcd's built-in unauthenticated session
    OBJECT = "labgrid-probe"

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        self._url = None

    def on_activate(self):
        service = self.service
        extra = getattr(service, "extra", None) or {}
        if extra.get("proxy_required"):
            # exporter started --isolated: forward through the exporter
            port = sshmanager.request_forward(
                extra["proxy"], service.address, service.port
            )
            self._url = f"http://localhost:{port}/ubus"
        else:
            # forwards through LG_PROXY if set, else direct
            self._url = proxymanager.get_url(
                f"http://{service.address}:{service.port}/ubus"
            )

    def on_deactivate(self):
        self._url = None

    @Driver.check_active
    def call_raw(self, method, **params):
        """Return (ubus status, reply data) of labgrid-probe.<method>.

        Raises ProbeError if the HTTP request fails or rpcd refuses it.
        """
        request = urllib.request.Request(
            self._url,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "call",
                    "params": [self.SESSION, self.OBJECT, method, params],
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.load(response)
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise ProbeError(f"probe at {self._url} not reachable: {error}") from error
        if "error" in body:
            message = body["error"].get("message", body["error"])
            raise ProbeError(f"probe refused {method}: {message} (rpcd ACL?)")
        result = body.get("result") or [-1]
        return result[0], (result[1] if len(result) > 1 else None)

    @Driver.check_active
    @step(args=["method"])
    def call(self, method, **params):
        """Call labgrid-probe.<method>; raise ProbeError unless it succeeded."""
        status, reply = self.call_raw(method, **params)
        if status != 0:
            raise ProbeError(f"labgrid-probe.{method} failed with ubus status {status}")
        if reply is None:
            raise ProbeError(f"labgrid-probe.{method} returned no data")
        if "error" in reply:
            raise ProbeError(f"labgrid-probe.{method}: {reply['error']}")
        return reply
