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
import os
from os import getenv
from pathlib import Path

import pytest

pytest_plugins = ["conftest_vlan"]

logger = logging.getLogger(__name__)


def _resolve_target_from_place():
    lg_env = getenv("LG_ENV")
    lg_place = getenv("LG_PLACE")

    if lg_env or not lg_place:
        return lg_env

    parts = lg_place.split("-", 2)
    if len(parts) < 3:
        return None

    device_instance = parts[2]

    try:
        repo_root = Path(__file__).parent.parent
        labnet_path = repo_root / "labnet.yaml"

        if not labnet_path.exists():
            return None

        import yaml

        with open(labnet_path, "r") as f:
            labnet = yaml.safe_load(f)

        if device_instance in labnet.get("devices", {}):
            device_config = labnet["devices"][device_instance]
            target_name = device_config.get("target_file", device_instance)
            target_file = f"targets/{target_name}.yaml"
            if (repo_root / target_file).exists():
                return str(repo_root / target_file)

        for lab_name, lab_config in labnet.get("labs", {}).items():
            device_instances = lab_config.get("device_instances", {})
            for base_device, instances in device_instances.items():
                if device_instance in instances:
                    if base_device in labnet.get("devices", {}):
                        device_config = labnet["devices"][base_device]
                        target_name = device_config.get("target_file", base_device)
                        target_file = f"targets/{target_name}.yaml"
                        if (repo_root / target_file).exists():
                            return str(repo_root / target_file)

    except Exception:
        pass

    return None


def pytest_configure(config):
    config._metadata = getattr(config, "_metadata", {})
    config._metadata["version"] = "12.3.4"
    config._metadata["environment"] = "staging"

    resolved_env = _resolve_target_from_place()
    if resolved_env:
        os.environ["LG_ENV"] = resolved_env


device = getenv("LG_ENV", "Unknown").split("/")[-1].split(".")[0]


def pytest_addoption(parser):
    parser.addoption("--firmware", action="store", default="firmware.bin")


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
