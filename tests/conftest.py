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
