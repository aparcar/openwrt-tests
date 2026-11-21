# SPDX-License-Identifier: LGPL-2.1-only
"""Daemons of a slot as transient systemd units (systemd-run).

Unit names are dprobe-<lan_vlan>-<service>. The unit runs the daemon in the
foreground under `ip netns exec`, so systemd tracks and stops it, and its
output lands in the journal (`dprobe logs`).
"""

import time

from dprobe.util import ProbeError, run


def unit(lan_vlan, name):
    return f"dprobe-{lan_vlan}-{name}.service"


def logs(lan_vlan, name, lines=50):
    proc = run(
        [
            "journalctl",
            "-u",
            unit(lan_vlan, name),
            "-n",
            lines,
            "-o",
            "cat",
            "--no-pager",
        ],
        check=False,
    )
    return proc.stdout.splitlines()


def status(lan_vlan, name):
    proc = run(
        [
            "systemctl",
            "show",
            unit(lan_vlan, name),
            "-p",
            "ActiveState,SubState,MainPID,Result",
        ],
        check=False,
    )
    props = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    return {
        "active": props.get("ActiveState") == "active",
        "state": props.get("SubState"),
        "pid": int(props.get("MainPID") or 0) or None,
        "result": props.get("Result"),
    }


def stop(lan_vlan, name):
    run(["systemctl", "stop", unit(lan_vlan, name)], check=False)
    run(["systemctl", "reset-failed", unit(lan_vlan, name)], check=False)


def _units(lan_vlan):
    proc = run(
        [
            "systemctl",
            "list-units",
            "--all",
            "--plain",
            "--no-legend",
            f"dprobe-{lan_vlan}-*.service",
        ],
        check=False,
    )
    return [line.split()[0] for line in proc.stdout.splitlines() if line.strip()]


def names(lan_vlan):
    prefix = f"dprobe-{lan_vlan}-"
    return [u.removeprefix(prefix).removesuffix(".service") for u in _units(lan_vlan)]


def stop_all(lan_vlan):
    for name in names(lan_vlan):
        stop(lan_vlan, name)


def status_all(lan_vlan):
    return {name: status(lan_vlan, name) for name in names(lan_vlan)}


def start(lan_vlan, name, netns, argv, settle=1.5):
    """(Re)start argv in netns as unit dprobe-<lan_vlan>-<name>. Raise
    ProbeError with the journal tail if it isn't running after `settle` s."""
    stop(lan_vlan, name)
    run(
        [
            "systemd-run",
            "--unit",
            unit(lan_vlan, name),
            "--collect",
            "--property=KillMode=mixed",
            "--",
            "ip",
            "netns",
            "exec",
            netns,
            *argv,
        ]
    )
    time.sleep(settle)
    st = status(lan_vlan, name)
    if not st["active"]:
        raise ProbeError(
            f"{name} did not start", status=st, log=logs(lan_vlan, name, 30)
        )
    return st
