# Copyright 2023 by Garmin Ltd. or its subsidiaries
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import re
import shlex
import subprocess
import time
from os import getenv

import pytest

logger = logging.getLogger(__name__)

device = getenv("LG_ENV", "Unknown").split("/")[-1].split(".")[0]


def pytest_addoption(parser):
    parser.addoption("--firmware", action="store", default="firmware.bin")


def pytest_configure(config):
    config._metadata = getattr(config, "_metadata", {})
    config._metadata["version"] = "12.3.4"
    config._metadata["environment"] = "staging"


def ubus_call(command, namespace, method, params={}):
    output = command.run_check(f"ubus call {namespace} {method} '{json.dumps(params)}'")

    try:
        return json.loads("\n".join(output))
    except json.JSONDecodeError:
        return {}


@pytest.fixture(scope="session", autouse=True)
def setup_env(env, pytestconfig):
    env.config.data.setdefault("images", {})["firmware"] = pytestconfig.getoption(
        "firmware"
    )


@pytest.fixture
def shell_command(strategy):
    try:
        strategy.transition("shell")
        return strategy.shell
    except Exception:
        logger.exception("Failed to transition to state shell")
        pytest.exit("Failed to transition to state shell", returncode=3)


@pytest.fixture
def ssh_command(shell_command, target):
    ssh = target.get_driver("SSHDriver")
    return ssh


def _host_ipv4_from_hostname_I() -> str:
    """Get the host's IPv4 address using hostname -I.

    This is needed for vwifi-client in the VM to connect back to the
    vwifi-server running on the host.
    """
    out = subprocess.check_output("hostname -I", shell=True, text=True).strip()
    if not out:
        raise RuntimeError("hostname -I returned nothing")
    # take the first token; if it's not IPv4, fall back to first IPv4 token
    first = out.split()[0]
    if ":" in first:
        first = next(
            (t for t in out.split() if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", t)), ""
        )
    if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", first or ""):
        raise RuntimeError(f"Could not determine IPv4 from: {out!r}")
    return first


@pytest.fixture
def upload_vwifi(shell_command, target):
    """Upload vwifi-client to the VM and connect to the virtualized mesh network.

    This fixture:
    1. Uploads vwifi-client binary to the target
    2. Configures mac80211_hwsim for virtual WiFi interfaces
    3. Starts vwifi-client to connect to the host's vwifi-server
    4. Waits for mesh interfaces to come up and establish connections

    Prerequisites:
    - vwifi-server must be running on the host
    - vwifi/vwifi-client binary must exist in the test directory
    """
    ssh = target.get_driver("SSHDriver")
    ssh.scp(src="vwifi/vwifi-client", dst=":/usr/bin/vwifi-client")
    path = "\n".join(ssh.run("which vwifi-client")[0])
    assert path == "/usr/bin/vwifi-client"

    # compute HOST IPv4 once (on the host)
    host_ip = _host_ipv4_from_hostname_I()
    host_ip_q = shlex.quote(host_ip)

    ssh.run_check("rmmod mac80211_hwsim")
    ssh.run_check("insmod mac80211_hwsim radios=0")
    cmd = f"""sh -lc '
        if command -v start-stop-daemon >/dev/null; then
          start-stop-daemon -S -b -m -p /tmp/vwifi.pid \
            -x /usr/bin/vwifi-client -- {host_ip_q} --number 2 \
            >/tmp/vwifi.log 2>&1
        else
          nohup /usr/bin/vwifi-client {host_ip_q} --number 2 \
            </dev/null >/tmp/vwifi.log 2>&1 & echo $! >/tmp/vwifi.pid
        fi
        '"""
    ssh.run_check(cmd)
    assert "\n".join(ssh.run("ps | grep vwifi")[0]) != ""
    time.sleep(5)
    ssh.run("wifi reload")
    ssh.run("wifi up")
    time.sleep(10)
    phy_devices = ssh.run("iw phy | grep phy")[0]
    assert len(phy_devices) == 4  # labgrid tokenizes on \t
    iw_devices = "\n".join(ssh.run("iw dev")[0])
    while "wlan0-mesh" not in iw_devices:
        iw_devices = "\n".join(ssh.run("iw dev")[0])
        time.sleep(2)
    stations = "\n".join(ssh.run("iw dev wlan0-mesh station dump")[0])
    assert "02:00:00:00:00:01" in stations
    assert "02:00:00:00:00:02" in stations
    assert "02:00:00:00:00:03" in stations
    return ssh
