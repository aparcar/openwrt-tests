"""labgrid driver for the labgrid-probe (contrib/labgrid-probe).

The probe exports a NetworkService for its HTTP endpoint (uhttpd's /ubus,
port 80). This driver calls the probe's "labgrid-probe" ubus object as
JSON-RPC with rpcd's unauthenticated session, which the probe's ACL limits to
exactly those methods. The connection goes through labgrid's proxy handling
(LG_PROXY / exporter proxy), like any other network resource.
"""

import json
import time
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
            # uhttpd-mod-ubus: "Object not found" = rpcd has no labgrid-probe
            # object (plugin not installed or failed to load); "Access denied"
            # = the ACL doesn't allow the method
            hint = {
                "Object not found": "rpcd plugin not loaded, run the labgrid_probe Ansible role",
                "Access denied": "check the rpcd ACL",
            }.get(message, "rpcd")
            raise ProbeError(f"probe refused {method}: {message} ({hint})")
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

    # --- per-slot helpers: every method takes the DUT's LAN VLAN first ------
    # Arguments that are None are left out, so the probe's defaults apply.

    def _slot_call(self, method, lan_vlan, **params):
        return self.call(
            method,
            lan_vlan=lan_vlan,
            **{k: v for k, v in params.items() if v is not None},
        )

    def wan_dhcp(self, lan_vlan, options=None, ntp=False):
        """Restart the DUT's WAN DHCP server with extra options (dnsmasq
        `--dhcp-option-force` syntax: "28,192.168.101.127" or
        "option:mtu,1400") and the NTP server on/off (announced as option 42).
        Replaces the previous options. The DUT sees them at its next request."""
        return self._slot_call(
            "wan_dhcp", lan_vlan, options=list(options or []), ntp=bool(ntp)
        )

    def speedtest_start(
        self,
        lan_vlan,
        duration=None,
        reverse=None,
        streams=None,
        udp=None,
        bitrate=None,
        server=None,
        port=None,
    ):
        """Start iperf3 from the LAN namespace (see speedtest())."""
        return self._slot_call(
            "speedtest_start",
            lan_vlan,
            duration=duration,
            reverse=reverse,
            streams=streams,
            udp=udp,
            bitrate=bitrate,
            server=server,
            port=port,
        )

    def speedtest_result(self, lan_vlan):
        return self._slot_call("speedtest_result", lan_vlan)

    def speedtest(self, lan_vlan, duration=10, poll=2.0, **opts):
        """Run iperf3 from the LAN namespace and wait for its result.

        Against the probe's WAN side by default (through the DUT's NAT), or
        `server` (an IPv4 address reachable from the LAN, e.g. the DUT running
        `iperf3 -s`). opts: reverse, streams, udp, bitrate ("100M"), server,
        port. Returns speedtest_result's reply (sent_bps, received_bps; TCP:
        retransmits; UDP: jitter_ms, lost_packets, packets, lost_percent).
        """
        self.speedtest_start(lan_vlan, duration=duration, **opts)
        deadline = time.monotonic() + duration + 60
        result = {"running": True}
        while result.get("running"):
            if time.monotonic() > deadline:
                raise ProbeError(f"speedtest did not finish within {duration + 60}s")
            time.sleep(poll)
            result = self.speedtest_result(lan_vlan)
        return result

    def ra_capture(self, lan_vlan, timeout=None, solicit=None, count=None):
        """Router Advertisements on the LAN, parsed: { ras: [{ router_lifetime,
        managed, other, preference, hop_limit, mtu, source, source_lladdr,
        prefixes: [{ prefix, length, on_link, autonomous, valid_lifetime,
        preferred_lifetime }], routes, rdnss: [{ address, lifetime }], dnssl,
        after_ms }] }. Sends a Router Solicitation first unless solicit is
        False; waits up to timeout s (1..20, default 5) for count RAs (default
        1; 0 = all within the timeout)."""
        return self._slot_call(
            "ra_capture", lan_vlan, timeout=timeout, solicit=solicit, count=count
        )

    def mcast_join(self, lan_vlan, group, port=None, side=None, igmp_version=None):
        """Join an IPv4 multicast group in the "lan" (default) or "wan"
        namespace and count the datagrams that arrive. igmp_version 2 or 3
        forces the joining interface's IGMP version (0: kernel default)."""
        return self._slot_call(
            "mcast_join",
            lan_vlan,
            group=group,
            port=port,
            side=side,
            igmp_version=igmp_version,
        )

    def mcast_send(
        self,
        lan_vlan,
        group,
        port=None,
        side=None,
        count=None,
        interval=None,
        ttl=None,
    ):
        """Send count datagrams (default 10, every interval ms, default 100)
        to group from the "wan" (default) or "lan" namespace, in the
        background. TTL default 8."""
        return self._slot_call(
            "mcast_send",
            lan_vlan,
            group=group,
            port=port,
            side=side,
            count=count,
            interval=interval,
            ttl=ttl,
        )

    def mcast_result(self, lan_vlan):
        """{ receiver: { joined, received, senders, first_seq, last_seq,
        running }, sender: { sent, errors, done, running } } (null if none)."""
        return self._slot_call("mcast_result", lan_vlan)

    def mcast_leave(self, lan_vlan):
        """Stop the receiver (IGMP leave) and sender; returns the last counts."""
        return self._slot_call("mcast_leave", lan_vlan)
