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

import inspect
import json
import logging
import os
import shlex
import shutil
import signal
import tempfile
from os import getenv
from pathlib import Path

import pytest

# The labgrid client holds a gRPC channel to the coordinator, so every ssh/scp
# the SSHDriver forks runs gRPC's pthread_atfork handler, which logs
# "FD from fork parent still in poll list" into ssh's stderr (surfacing as
# SSHDriver warnings) and can race the child before exec. The children exec
# immediately and never reuse the channel, so fork support isn't needed.
# See https://github.com/grpc/grpc/blob/master/doc/fork_support.md
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "0")

logger = logging.getLogger(__name__)

# Fixtures shared by several modules:
# - hwsim radios on QEMU targets with the `hwsim` feature
# - the lab's labgrid-probe (contrib/labgrid-probe)
from hwsim_setup import hwsim  # noqa: F401
from probe_fixtures import probe  # noqa: F401


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

        for lab_config in labnet.get("labs", {}).values():
            device_instances = lab_config.get("device_instances", {})
            for base_device, instances in device_instances.items():
                if device_instance in instances and base_device in labnet.get(
                    "devices", {}
                ):
                    device_config = labnet["devices"][base_device]
                    target_name = device_config.get("target_file", base_device)
                    target_file = f"targets/{target_name}.yaml"
                    if (repo_root / target_file).exists():
                        return str(repo_root / target_file)

    except Exception:
        logger.exception("Failed to resolve LG_ENV from LG_PLACE")

    return None


def pytest_configure(config):
    config._metadata = getattr(config, "_metadata", {})
    config._metadata["version"] = "12.3.4"
    config._metadata["environment"] = "staging"

    resolved_env = _resolve_target_from_place()
    if resolved_env:
        os.environ["LG_ENV"] = resolved_env


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    # test_base.py first, in its own order (the basics: boot, shell, SSH,
    # kernel log), then the other modules as collected (by file name). The
    # sort is stable, so tests keep their order within a module.
    items.sort(key=lambda item: item.path.name != "test_base.py")

    # Tests using the `online` fixture (probe_fixtures.py), directly or
    # through another fixture, need the labgrid feature `online` (the DUT has
    # a WAN uplink): add the mark before labgrid's hook skips on features
    for item in items:
        if "online" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.lg_feature("online"))

    # The docstrings of the test and its module (what they check and why) as
    # the JUnit properties `description` and `module_description`:
    # labgrid-agent stores them with the results and the web UI shows them.
    # Recorded at collection, so skipped tests have them too.
    for item in items:
        for name, obj in (
            ("description", getattr(item, "obj", None)),
            ("module_description", getattr(item, "module", None)),
        ):
            doc = getattr(obj, "__doc__", None)
            if doc and doc.strip():
                item.user_properties.append((name, inspect.cleandoc(doc)))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    yield
    drain_consoles(item.config)


# What drain_consoles reads at most per console and test
DRAIN_MAX_BYTES = 1 << 20


def drain_consoles(config):
    """Read what the devices printed on their serial consoles meanwhile.

    labgrid logs console output (log level CONSOLE) only when a driver
    reads it. A test that only uses SSH reads nothing, so kernel messages
    from its time (driver warnings, oopses) would sit in the serial buffer
    and show up in a later test's log, or never. Reading them at the end of
    the test puts them into this test's captured log. Only consoles that
    are already active: no test gets a console it didn't ask for."""
    import pexpect
    from labgrid.exceptions import NoDriverFoundError
    from labgrid.protocol import ConsoleProtocol
    from labgrid.pytestplugin.hooks import LABGRID_ENV_KEY

    env = config.stash.get(LABGRID_ENV_KEY, None)
    if env is None:
        return
    for target in list(env.targets.values()):
        try:
            console = target.get_active_driver(ConsoleProtocol)
        except NoDriverFoundError:  # no console, or none active
            logger.debug("no active console on %s", target.name)
            continue
        read = 0
        while read < DRAIN_MAX_BYTES:
            try:
                read += len(console.read(size=1, timeout=0.2, max_size=65536))
            except pexpect.TIMEOUT:
                break
            except OSError:  # a console that went away is not this test's failure
                logger.debug("draining %s failed", console, exc_info=True)
                break


device = getenv("LG_ENV", "Unknown").split("/")[-1].split(".")[0]


def pytest_addoption(parser):
    parser.addoption("--firmware", action="store", default="firmware.bin")
    parser.addoption(
        "--bios", action="store", help="UEFI firmware for EFI targets (QEMU -bios)"
    )


# UEFI firmware (edk2) for QEMU's -bios, for targets with `openwrt.efi_firmware`.
# Bare names are looked up in QEMU's data directories (Homebrew, upstream
# install); absolute paths are the Debian packages (in QEMU_EFI_PACKAGES).
QEMU_DATA_DIRS = [
    "/opt/homebrew/share/qemu",
    "/usr/local/share/qemu",
    "/usr/share/qemu",
]
QEMU_EFI_FIRMWARE = {
    "aarch64": ["edk2-aarch64-code.fd", "/usr/share/AAVMF/AAVMF_CODE.fd"],
    "arm": ["edk2-arm-code.fd", "/usr/share/AAVMF/AAVMF32_CODE.fd"],
    "x86_64": ["edk2-x86_64-code.fd", "/usr/share/OVMF/OVMF_CODE_4M.fd"],
    "i386": ["edk2-i386-code.fd", "/usr/share/OVMF/OVMF32_CODE_4M.fd"],
    "loongarch64": [
        "edk2-loongarch64-code.fd",
        "/usr/share/qemu-efi-loongarch64/QEMU_EFI.fd",
    ],
}
# x86 (OVMF) firmware is a code-only image too small for -bios: QEMU refuses it
# as a PC BIOS. It runs from pflash instead, with a variable store next to it
# (a copy of these templates, so the system files stay untouched).
QEMU_EFI_VARS = {
    "x86_64": ["edk2-i386-vars.fd", "/usr/share/OVMF/OVMF_VARS_4M.fd"],
    "i386": ["edk2-i386-vars.fd", "/usr/share/OVMF/OVMF32_VARS_4M.fd"],
    # armv7: with -bios (no variable store) edk2 stalls after its banner in
    # some boots
    "arm": ["edk2-arm-vars.fd", "/usr/share/AAVMF/AAVMF32_VARS.fd"],
}
QEMU_EFI_PACKAGES = {
    "aarch64": "qemu-efi-aarch64",
    "arm": "qemu-efi-arm",
    "x86_64": "ovmf",
    "i386": "ovmf-ia32",
    "loongarch64": "qemu-efi-loongarch64",
}


def _find_qemu_file(names):
    for name in names:
        if name.startswith("/"):
            candidates = [Path(name)]
        else:
            candidates = [Path(d, name) for d in QEMU_DATA_DIRS]
        for path in candidates:
            if path.is_file():
                return str(path)
    return None


def find_efi_firmware(arch):
    return _find_qemu_file(QEMU_EFI_FIRMWARE.get(arch, []))


def ubus_call(command, namespace, method, params=None):
    if params is None:
        params = {}
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

    # EFI targets boot their disk image through a UEFI firmware: Arm and
    # LoongArch via the QEMUDriver `bios: bios` image, x86 via two pflash
    # drives (code, vars)
    vars_dir = None
    arch = env.config.data.get("openwrt", {}).get("efi_firmware")
    if arch:
        bios = pytestconfig.getoption("bios") or find_efi_firmware(arch)
        vars_template = _find_qemu_file(QEMU_EFI_VARS.get(arch, []))
        if not bios or (arch in QEMU_EFI_VARS and not vars_template):
            pytest.exit(
                f"no UEFI firmware for {arch} found, install "
                f"{QEMU_EFI_PACKAGES.get(arch, 'edk2')} or pass --bios",
                returncode=pytest.ExitCode.USAGE_ERROR,
            )
        bios = os.path.abspath(bios)
        if arch in QEMU_EFI_VARS:
            vars_dir = tempfile.mkdtemp(prefix="openwrt-efi-vars-")
            efi_vars = Path(vars_dir, "vars.fd")
            shutil.copyfile(vars_template, efi_vars)
            # labgrid's env fixture has already created the QEMUDriver, which
            # copied extra_args from the config: change the driver itself
            qemu = env.get_target("main").get_driver("QEMUDriver", activate=False)
            qemu.extra_args = " ".join(
                [
                    qemu.extra_args,
                    "-drive",
                    shlex.quote(f"if=pflash,format=raw,unit=0,readonly=on,file={bios}"),
                    "-drive",
                    shlex.quote(f"if=pflash,format=raw,unit=1,file={efi_vars}"),
                ]
            )
        else:
            env.config.data["images"]["bios"] = bios

    yield
    if vars_dir:
        shutil.rmtree(vars_dir, ignore_errors=True)


@pytest.fixture
def shell_command(env, strategy):
    # The first call boots the DUT. Targets whose boot doesn't fit into the
    # per-test timeout (e.g. CFETFTPStrategy flashing the image) set
    # openwrt.boot_timeout: the transition runs on that budget instead, and
    # the test gets the time it had left afterwards
    boot_timeout = (env.config.data.get("openwrt") or {}).get("boot_timeout")
    remaining, interval = signal.getitimer(signal.ITIMER_REAL)
    budget = bool(boot_timeout and remaining)
    if budget:
        signal.setitimer(signal.ITIMER_REAL, float(boot_timeout))
    try:
        strategy.transition("shell")
        return strategy.shell
    except Exception:
        logger.exception("Failed to transition to state shell")
        pytest.exit("Failed to transition to state shell", returncode=3)
    finally:
        if budget:
            signal.setitimer(signal.ITIMER_REAL, remaining, interval)


@pytest.fixture
def ssh_command(shell_command, target):
    ssh = target.get_driver("SSHDriver")
    return ssh
