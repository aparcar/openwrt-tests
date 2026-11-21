# SPDX-License-Identifier: LGPL-2.1-only
"""Shared helpers: errors, exit codes, subprocesses, probe configuration."""

import json
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger("dprobe")

# Exit codes, shared with drivers/debianprobedriver.py
OK = 0
FAILED = 1  # ran fine, but the expected thing didn't happen
ERROR = 2  # couldn't run: bad arguments, no slot, a command failed

CONFIG_FILE = Path(os.environ.get("DPROBE_CONFIG", "/etc/dprobe/dprobe.json"))
RUN_DIR = Path(os.environ.get("DPROBE_RUN_DIR", "/run/dprobe"))


class ProbeError(Exception):
    """Exit with ERROR and this message as {"error": ...}."""

    def __init__(self, message, **extra):
        super().__init__(message)
        self.extra = extra


class ProbeFailed(Exception):
    """Exit with FAILED and this reply (a dict) on stdout."""

    def __init__(self, reply):
        super().__init__(reply.get("error", "failed"))
        self.reply = reply


def load_config():
    """The probe's static config (rendered by the Ansible role).

    trunk: the interface carrying the DUT VLANs (default: the default
           route's interface, like the OpenWrt probe)
    allowed_vlans: VLANs tests may attach to; empty means any except
           mgmt_vlan
    mgmt_vlan: never attach to this one
    """
    try:
        return json.loads(CONFIG_FILE.read_text())
    except FileNotFoundError:
        return {}
    except ValueError as e:
        raise ProbeError(f"{CONFIG_FILE}: {e}") from e


def run(argv, check=True, timeout=30, input=None):
    """Run argv, return CompletedProcess (text). Raise ProbeError on failure."""
    log.debug("run: %s", " ".join(map(str, argv)))
    try:
        proc = subprocess.run(
            [str(a) for a in argv],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input,
        )
    except FileNotFoundError as e:
        raise ProbeError(f"{argv[0]} not installed") from e
    except subprocess.TimeoutExpired as e:
        raise ProbeError(f"timeout after {timeout}s: {' '.join(map(str, argv))}") from e
    if check and proc.returncode != 0:
        raise ProbeError(
            f"{' '.join(map(str, argv))} failed ({proc.returncode})",
            stderr=proc.stderr.strip()[-2000:],
        )
    return proc


def ip_json(*args, netns=None):
    """`ip -j <args>` (optionally inside netns) as parsed JSON."""
    argv = ["ip"]
    if netns:
        argv += ["-n", netns]
    argv += ["-j", *args]
    out = run(argv).stdout.strip()
    return json.loads(out) if out else []


def write_file(path, content, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content)
    tmp.chmod(mode)
    tmp.replace(path)
    return path


def vlan_id(value):
    """Parse a VLAN id (1..4094) or raise ProbeError."""
    try:
        vid = int(value)
    except (TypeError, ValueError):
        vid = 0
    if not 1 <= vid <= 4094:
        raise ProbeError(f"bad VLAN id {value!r}: must be 1..4094")
    return vid


def mac_for(kind, vlan):
    """Stable locally administered MAC: 02:44:50 ("DP"), kind, VLAN."""
    return f"02:44:50:{kind:02x}:{vlan >> 8:02x}:{vlan & 0xFF:02x}"
