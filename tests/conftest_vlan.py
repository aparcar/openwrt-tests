"""
Optional dynamic VLAN switching fixtures for multi-node tests.

When enabled, the ``shared_vlan_multi`` fixture moves all DUT ports listed
in ``LG_MULTI_PLACES`` to a shared VLAN before the test session and restores
them on teardown. Topology changes are delegated to the ``switch-vlan`` CLI
from `labgrid-switch-abstraction
<https://github.com/fcefyn-testbed/labgrid-switch-abstraction>`_.

Execution model
---------------

- **Remote** (``LG_PROXY`` set): the command is sent via SSH to the proxy
  host. The lab host owns the switch credentials (``switch.conf``) and the
  DUT-to-port map (``dut-config.yaml``). Developer machines need nothing
  beyond SSH access to the proxy.
- **Local** (``LG_PROXY`` unset): ``switch-vlan`` runs on the current host.
  Requires ``switch-vlan`` in PATH (``pip install labgrid-switch-abstraction``)
  and a configured ``~/.config/switch.conf`` / ``dut-config.yaml``.

Activation
----------

Set ``VLAN_SWITCH_ENABLED=1`` to opt in. Default: skipped.

Configuration (env vars)
------------------------

- ``LG_MULTI_PLACES``: Comma-separated labgrid place names to switch.
- ``VLAN_SHARED``:     Target VLAN ID for multi-node tests (default 200).
- ``PLACE_PREFIX``:    Strip this prefix from place names to derive the DUT
                       name passed to ``switch-vlan``. If unset, everything
                       up to and including the second hyphen is stripped
                       (e.g. ``labgrid-mylab-router_1`` -> ``router_1``).

See ``docs/switch-abstraction.md`` for full setup instructions.
"""

import logging
import os
import shutil
import subprocess

import pytest

logger = logging.getLogger(__name__)

VLAN_SHARED_DEFAULT = 200
SSH_TIMEOUT = 30


def _is_enabled() -> bool:
    return os.environ.get("VLAN_SWITCH_ENABLED", "").lower() in ("1", "true", "yes")


def _resolve_proxy_host() -> str | None:
    """Extract the SSH host from ``LG_PROXY`` (e.g. ``ssh://host`` -> ``host``)."""
    raw = os.environ.get("LG_PROXY", "").strip()
    if not raw:
        return None
    return raw.removeprefix("ssh://")


def _vlan_shared() -> int:
    raw = os.environ.get("VLAN_SHARED", "").strip()
    if not raw:
        return VLAN_SHARED_DEFAULT
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "Invalid VLAN_SHARED=%r, falling back to %d", raw, VLAN_SHARED_DEFAULT
        )
        return VLAN_SHARED_DEFAULT


def _place_to_dut_name(place: str) -> str:
    """Map a labgrid place name to the DUT name expected by ``switch-vlan``.

    If ``PLACE_PREFIX`` is set it is stripped. Otherwise everything up to and
    including the second hyphen is stripped (default openwrt-tests convention
    ``labgrid-<lab>-<dut>``).
    """
    prefix = os.environ.get("PLACE_PREFIX", "")
    if prefix and place.startswith(prefix):
        return place[len(prefix) :]
    parts = place.split("-", 2)
    if len(parts) == 3:
        return parts[2]
    return place


def _run_switch_vlan(args: list[str]) -> bool:
    """Run ``switch-vlan`` via SSH to ``LG_PROXY``, or locally if no proxy is set.

    Rationale: ``LG_PROXY`` indicates the lab is at a remote host, so the VLAN
    command must run there (the proxy host owns switch credentials and the
    DUT-to-port map). Only fall back to local execution when no proxy is set
    (lab host or CI runner).

    Returns ``True`` on success, ``False`` on failure (logged, never raises).
    """
    proxy = _resolve_proxy_host()
    if proxy:
        cmd = ["ssh", proxy, "switch-vlan " + " ".join(args)]
    elif shutil.which("switch-vlan"):
        cmd = ["switch-vlan", *args]
    else:
        logger.warning(
            "LG_PROXY not set and switch-vlan not in PATH; skipping VLAN command. "
            "Install with: pip install labgrid-switch-abstraction"
        )
        return False

    logger.debug("VLAN command: %s", cmd)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        logger.error("switch-vlan timed out after %ds: %s", SSH_TIMEOUT, cmd)
        return False

    if result.returncode != 0:
        stderr = result.stderr.strip()
        hint = ""
        if "password required" in stderr.lower() and proxy:
            hint = (
                f"\nHint: switch-vlan ran on '{proxy}' as the SSH user. "
                f"That user needs a readable switch.conf "
                f"(per-user '~/.config/switch.conf' or system-wide "
                f"'/etc/switch.conf' with group access). "
                f"See docs/switch-abstraction.md."
            )
        logger.error(
            "switch-vlan failed (rc=%d): %s\nstderr: %s%s",
            result.returncode,
            cmd,
            stderr,
            hint,
        )
        return False

    logger.debug("switch-vlan stdout: %s", result.stdout.strip())
    return True


@pytest.fixture(scope="session")
def shared_vlan_multi():
    """Switch all multi-node DUT ports to the shared VLAN.

    Activates when ``VLAN_SWITCH_ENABLED=1`` and ``LG_MULTI_PLACES`` is set
    (comma-separated). Session-scoped: switches once for the test session,
    restores on teardown. Returns the list of DUT names that were switched.
    """
    if not _is_enabled():
        yield []
        return

    places_str = os.environ.get("LG_MULTI_PLACES", "")
    if not places_str:
        yield []
        return

    places = [p.strip() for p in places_str.split(",") if p.strip()]
    dut_names = [_place_to_dut_name(p) for p in places]

    vlan = _vlan_shared()
    logger.info(
        "Switching %d DUTs to shared VLAN %d: %s",
        len(dut_names),
        vlan,
        dut_names,
    )
    ok = _run_switch_vlan([*dut_names, str(vlan)])
    if not ok:
        pytest.fail(f"VLAN switch to {vlan} failed for DUTs: {dut_names}")

    yield dut_names

    logger.info("Restoring %d DUTs to isolated VLANs: %s", len(dut_names), dut_names)
    _run_switch_vlan([*dut_names, "--restore"])
