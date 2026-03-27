# LibreMesh shared-state-async tests using virtualized mesh (vwifi)
#
# These tests verify that LibreMesh's shared-state-async mechanism works
# correctly in a virtualized mesh network environment.
#
# Prerequisites:
#   - vwifi-server running on the host with multiple simulated nodes
#   - LibreMesh firmware image with shared-state-async package
#   - vwifi-client binary available in vwifi/ directory
#
# Usage:
#   pytest tests/test_libremesh_shared_state_async.py -k libremesh

import json
import logging
import time

import pytest

# Expected node hostnames and MAC addresses in the virtualized mesh
N1 = "LiMe-000001"
N2 = "LiMe-000002"
N3 = "LiMe-000003"
N1234 = "LiMe-123456"
MAC1 = "02:58:47:00:00:01"
MAC2 = "02:58:47:00:00:02"
MAC3 = "02:58:47:00:00:03"
MAC1234 = "02:58:47:12:34:56"


logger = logging.getLogger(__name__)


def _join_stdout(stdout):
    """Join stdout lines into a single string."""
    if isinstance(stdout, (list, tuple)):
        return "\n".join(stdout)
    return stdout or ""


def _extract_json_from_mixed(text):
    """Extract JSON object from mixed text output."""
    i = text.find("{")
    j = text.rfind("}")
    assert i != -1 and j != -1 and j > i, f"Could not find JSON in output:\n{text}"
    return json.loads(text[i : j + 1])


def _strip_mac(mac):
    """Remove colons from MAC address and lowercase."""
    return mac.replace(":", "").lower()


def _canonical_link_key(mac_a, mac_b):
    """Create canonical link key from two MAC addresses."""
    a = _strip_mac(mac_a)
    b = _strip_mac(mac_b)
    return "".join(sorted([a, b]))


@pytest.mark.lg_feature("libremesh")
def test_bat_links_info(upload_vwifi):
    """Test that shared-state-async bat_links_info shows all mesh nodes and links.

    This test verifies that:
    1. shared-state-async can publish and sync bat_links_info
    2. All expected mesh nodes are visible in the shared state
    3. Links between the test node and other nodes are recorded
    """
    ssh_command = upload_vwifi
    link_key_N1234_N1 = _canonical_link_key(MAC1234, MAC1)
    link_key_N1234_N2 = _canonical_link_key(MAC1234, MAC2)
    link_key_N1234_N3 = _canonical_link_key(MAC1234, MAC3)

    ssh_command.run_check("shared-state-async-publish-all")
    ssh_command.run_check("shared-state-async sync bat_links_info")
    time.sleep(15)
    out, err, rc = ssh_command.run("shared-state-async get bat_links_info")
    assert rc == 0, f"shared-state-async failed (rc={rc}) stderr={_join_stdout(err)}"
    data = _extract_json_from_mixed(_join_stdout(out))

    assert isinstance(data, dict) and data, "bat_links_info must be a non-empty dict"
    logger.warning(out)
    assert N1234 in data, f"Expected {N1234} in shared-state keys: {list(data.keys())}"
    assert N1 in data, f"Expected {N1} in shared-state keys: {list(data.keys())}"
    assert N2 in data, f"Expected {N2} in shared-state keys: {list(data.keys())}"
    assert N3 in data, f"Expected {N3} in shared-state keys: {list(data.keys())}"
    assert link_key_N1234_N1 in data[N1234]["links"], (
        f"Expected {link_key_N1234_N1} in shared-state keys: {list(data[N1234]['links'])}"
    )
    assert link_key_N1234_N2 in data[N1234]["links"], (
        f"Expected {link_key_N1234_N2} in shared-state keys: {list(data[N1234]['links'])}"
    )
    assert link_key_N1234_N3 in data[N1234]["links"], (
        f"Expected {link_key_N1234_N3} in shared-state keys: {list(data[N1234]['links'])}"
    )
