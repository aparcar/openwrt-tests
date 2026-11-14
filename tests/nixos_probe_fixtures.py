"""Fixtures for the lab's NixOS probe (contrib/nixos-probe).

The NixOS probe does what the OpenWrt probe (contrib/labgrid-probe) can't:
DHCPv6 with prefix delegation (kea), raw L2 and scapy, and any other tool
from nixpkgs. Tests log in over SSH (labgrid SSHDriver on the probe place's
NetworkService) and run `nixos-probe` and their own commands in this DUT's
slot, two network namespaces on the DUT's VLANs, as on the OpenWrt probe.

The probe is a labgrid place of its own (labgrid-<lab>-nixos-probe, tag
role=nixos-probe; labnet.yaml `nixos_probe: true`), reserved and locked like
the OpenWrt probe (probe_fixtures.hold_probe_place).

A test module imports the fixtures it uses:

    from nixos_probe_fixtures import nixos_probe, nixos_slot, nixos_wan_slot  # noqa: F401

- `nixos_probe`: the probe's SSHDriver, locked for the module.
- `nixos_slot`: this DUT's slot (LAN side: a client namespace with SLAAC
  from the DUT's RAs). Skips on places without a LAN link (QEMU).
- `nixos_wan_slot`: the slot with the WAN side up: radvd and kea-dhcp6 hand
  the DUT's wan6 an address and a delegated prefix from
  2001:db8:<wan id>::/48; waits for the DUT to take the prefix. Skips
  without a cabled WAN port.
"""

import ipaddress
import json
import logging
import shlex
import time

import pytest
from probe_fixtures import hold_probe_place, interface_status, lan_link, wan_link

logger = logging.getLogger(__name__)

NIXOS_PROBE_TAGS = {"role": "nixos-probe"}


class NixosProbeError(Exception):
    """`nixos-probe` failed or answered with an error."""


class NixosSlot:
    """This DUT's slot on the NixOS probe.

    `slot.lan("ip -6 addr")`, `slot.wan(...)`: run a command in the LAN or
    WAN namespace, (stdout, stderr, rc) as from SSHDriver.run;
    `lan_check`/`wan_check` raise on failure. `slot.scapy("lan", code)` runs
    Python with scapy there. `slot.info` holds `nixos-probe`'s reply (the
    WAN side's addressing under "ipv6").
    """

    def __init__(self, ssh, info):
        self.ssh = ssh
        self.slot = info["slot"]
        self.info = info

    def probe(self, args, timeout=60):
        """Run `nixos-probe <args>` and return its JSON reply."""
        return nixos_probe_call(self.ssh, f"{args} --slot {self.slot}", timeout)

    def _exec(self, side, command):
        return f"nixos-probe exec --slot {self.slot} {side} -- {command}"

    def lan(self, command, timeout=60):
        return self.ssh.run(self._exec("lan", command), timeout=timeout)

    def wan(self, command, timeout=60):
        return self.ssh.run(self._exec("wan", command), timeout=timeout)

    def lan_check(self, command, timeout=60):
        return self.ssh.run_check(self._exec("lan", command), timeout=timeout)

    def wan_check(self, command, timeout=60):
        return self.ssh.run_check(self._exec("wan", command), timeout=timeout)

    def scapy(self, side, code, timeout=60):
        """Run `code` (Python, `from scapy.all import *` done) in a namespace
        and return its stdout lines."""
        script = "from scapy.all import *\nconf.verb = 0\n" + code
        return self.ssh.run_check(
            self._exec(side, f"python3 -c {shlex.quote(script)}"), timeout=timeout
        )

    def leases(self):
        """kea-dhcp6's active leases: [{type: na|pd, address, duid, ...}]."""
        return self.probe("leases")["leases"]


def nixos_probe_call(ssh, args, timeout=60):
    stdout, stderr, rc = ssh.run(f"nixos-probe {args}", timeout=timeout)
    try:
        reply = json.loads("\n".join(stdout))
    except json.JSONDecodeError:
        raise NixosProbeError(
            f"nixos-probe {args}: rc {rc}: {stdout} {stderr}"
        ) from None
    if rc != 0 or "error" in reply:
        raise NixosProbeError(f"nixos-probe {args}: {reply.get('error', reply)}")
    return reply


@pytest.fixture(scope="module")
def nixos_probe(env, target):
    """The NixOS probe's SSHDriver, locked for the requesting module.

    Skips without a coordinator (QEMU) or if the lab has no NixOS probe (no
    place tagged role=nixos-probe).
    """
    with hold_probe_place(
        env, target, NIXOS_PROBE_TAGS, "SSHDriver", "nixos-probe"
    ) as ssh:
        yield ssh


# Reserved last, as in probe_fixtures.py: only once the DUT has the links the
# tests need and has booted.


@pytest.fixture(scope="module")
def nixos_slot(request, strategy, target):
    dev, vlan = lan_link(target)
    strategy.transition("shell")
    ssh = request.getfixturevalue("nixos_probe")
    args = f"up --lan-vlan {vlan}" if vlan is not None else f"up --parent {dev}"
    slot = NixosSlot(ssh, nixos_probe_call(ssh, args))
    yield slot
    try:
        nixos_probe_call(ssh, f"down --slot {slot.slot}")
    except NixosProbeError as e:
        logger.warning("removing slot %s: %s", slot.slot, e)


def wan6_prefix(shell, pool):
    """The DUT's delegated prefix from `pool` (wan6's ipv6-prefix), or None."""
    out, _, rc = shell.run("ubus call network.interface.wan6 status")
    if rc != 0:
        return None
    status = json.loads("\n".join(out))
    for p in status.get("ipv6-prefix", []):
        prefix = ipaddress.ip_network(f"{p['address']}/{p['mask']}")
        if prefix.subnet_of(pool):
            return prefix
    return None


def wait_for_wan6_prefix(shell, pool, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        prefix = wan6_prefix(shell, pool)
        if prefix:
            return prefix
        time.sleep(3)
    status, _, _ = shell.run(
        "ubus call network.interface.wan6 status; logread -e odhcp6c"
    )
    pytest.fail(
        f"DUT's wan6 got no prefix from {pool} within {timeout}s:\n" + "\n".join(status)
    )


@pytest.fixture(scope="module")
def nixos_wan_slot(request, strategy, target):
    """The slot with the WAN side up; `slot.prefix` is the DUT's delegated
    prefix, `slot.pd_pool` the pool it came from."""
    dev, vlan = wan_link(target)
    slot = request.getfixturevalue("nixos_slot")
    args = f"--wan-vlan {vlan}" if vlan is not None else f"--wan-parent {dev}"
    slot.info = slot.probe(f"wan-up {args}")
    slot.pd_pool = ipaddress.ip_network(slot.info["ipv6"]["pd_pool"])
    shell = strategy.shell
    # wan6 is only up once it has a lease; wait for netifd to know it
    deadline = time.monotonic() + 120
    while interface_status(shell, "wan6") is None:
        if time.monotonic() > deadline:
            pytest.skip("DUT has no wan6 interface")
        time.sleep(3)
    # A fresh solicit: odhcp6c would keep an earlier upstream's prefix
    # until it renews
    shell.run_check("ifup wan6")
    slot.prefix = wait_for_wan6_prefix(shell, slot.pd_pool)
    yield slot
    # The DUT's next upstream (the OpenWrt probe, another module) gets a
    # fresh solicit too; the prefix from here would otherwise linger
    shell.run("ifup wan6")
