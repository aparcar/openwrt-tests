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
from os import getenv, path
from pathlib import Path

import allure
import pytest
from pytest_harvest import get_fixture_store

logger = logging.getLogger(__name__)

device = getenv("LG_ENV", "Unknown").split("/")[-1].split(".")[0]


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    result = outcome.get_result()

    if result.when == "call":
        allure.dynamic.parent_suite(device)


def pytest_addoption(parser):
    parser.addoption("--firmware", action="store", default="firmware.bin")


def pytest_sessionfinish(session):
    """Gather all results and save them to a JSON file."""

    fixture_store = get_fixture_store(session)
    if "results_bag" not in fixture_store:
        return

    results = fixture_store["results_bag"]

    Path("results.json").write_text(json.dumps(results, indent=2))

    alluredir = session.config.getoption("--alluredir")

    if not alluredir or not path.isdir(alluredir):
        return

    # workaround for allure to accept multiple devices as suites
    for json_file in Path(alluredir).glob("*.json"):
        json_data = json.loads(json_file.read_text())
        if "testCaseId" in json_data:
            json_data["testCaseId"] = device + json_data["testCaseId"].split(".")[-1]
            json_data["historyId"] = device + json_data["historyId"].split(".")[-1]
        json_file.write_text(json.dumps(json_data))

    allure_properties_file = Path(alluredir, "environment.properties")
    allure_properties_file.write_text(
        f"Version={results['tests/test_base.py::test_ubus_system_board']['version']}\n"
        f"Revision={results['tests/test_base.py::test_ubus_system_board']['revision']}\n"
    )


def ubus_call(command, namespace, method, params={}):
    output, _, exitcode = command.run(
        f"ubus call {namespace} {method} '{json.dumps(params)}'"
    )
    assert exitcode == 0

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
