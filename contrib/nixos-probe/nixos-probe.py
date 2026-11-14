#!/usr/bin/env python3
"""nixos-probe: per-DUT network namespaces on the NixOS probe.

Tests run this over SSH (tests/nixos_probe_fixtures.py). Like the OpenWrt
probe (contrib/labgrid-probe), a slot is two network namespaces on the DUT's
VLANs, so traffic between them has to cross the DUT:

- np-lan<slot>: a client on the DUT's LAN (SLAAC from the DUT's RAs).
- np-wan<slot>: the DUT's IPv6 upstream: radvd and kea-dhcp6 hand out an
  address (IA_NA) and a delegated prefix (IA_PD) from 2001:db8:<wan>::/48.

Everything else (scapy, dhcpcd, tcpdump, iperf3, ...) the tests run
themselves in a namespace: `nixos-probe exec --slot <slot> lan -- <cmd>`.

Each side sits on the trunk as a VLAN device (`--lan-vlan`, `--wan-vlan`),
or, on a lab host that is the probe itself, as a private macvlan on the
host's interface towards the DUT (`--parent`, `--wan-parent`). Parents must
be listed in the config (`parents`); the trunk defaults to the port the
default route uses.

Every command prints one JSON object. Errors: {"error": "..."} and exit 1.
"""

import argparse
import csv
import ipaddress
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

CONFIG = Path(os.environ.get("NIXOS_PROBE_CONFIG", "/etc/nixos-probe.json"))
RUN = Path(os.environ.get("NIXOS_PROBE_RUN", "/run/nixos-probe"))
# Slot ids for parents without a VLAN: 4000 + the parent's ifindex, as the
# OpenWrt probe's local mode does
SLOT_OFFSET = 4000
# The DUT's WAN side: 2001:db8:<wan id>::/48; the link is its first /64,
# delegated prefixes come from 2001:db8:<wan id>:100::/56
PD_POOL_SUBNET = 0x100
DEFAULT_DELEGATED_LEN = 60
DAEMONS = ("kea-dhcp6", "radvd")


class ProbeError(Exception):
    pass


def load_config():
    try:
        return json.loads(CONFIG.read_text())
    except FileNotFoundError:
        return {}


def run(*cmd, check=True, ns=None):
    if ns:
        cmd = ("ip", "netns", "exec", ns, *cmd)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise ProbeError(f"{' '.join(cmd)}: {proc.stderr.strip() or proc.returncode}")
    return proc


def ip_json(*args, ns=None):
    out = run("ip", "-j", *args, ns=ns).stdout
    return json.loads(out) if out.strip() else []


def link_exists(dev):
    return run("ip", "link", "show", "dev", dev, check=False).returncode == 0


def ifindex(dev):
    return ip_json("link", "show", "dev", dev)[0]["ifindex"]


def namespaces():
    return {n["name"] for n in ip_json("netns", "list")}


def default_trunk():
    """The port the default route uses: the one carrying the management
    network, which also carries the tagged DUT VLANs."""
    for route in ip_json("route", "show", "default"):
        if route.get("dev"):
            return route["dev"]
    raise ProbeError("no default route to take the trunk from; pass --trunk")


def mac(side, slot):
    """Locally administered, per side and slot: 02:6e:70:<side>:<slot>."""
    return f"02:6e:70:{side:02x}:{slot >> 8:02x}:{slot & 0xFF:02x}"


def attach(ns, side, slot, vlan, parent, config):
    """Create the namespace's eth0: VLAN `vlan` on the trunk, or a private
    macvlan on `parent`. Returns the details for the reply."""
    tmp = f"np{'lw'[side - 1]}{slot}"
    if vlan is not None:
        trunk = parent or config.get("trunk") or default_trunk()
        run(
            "ip",
            "link",
            "add",
            "link",
            trunk,
            "name",
            tmp,
            "type",
            "vlan",
            "id",
            str(vlan),
        )
        info = {"trunk": trunk, "vlan": vlan}
    else:
        allowed = config.get("parents") or []
        if parent not in allowed:
            raise ProbeError(f"parent {parent} is not in the probe's parents {allowed}")
        # private: the namespace talks to the DUT only, not to the host's own
        # address on the parent
        run(
            "ip",
            "link",
            "add",
            "link",
            parent,
            "name",
            tmp,
            "type",
            "macvlan",
            "mode",
            "private",
        )
        info = {"parent": parent}
    run("ip", "link", "set", tmp, "netns", ns)
    run("ip", "link", "set", tmp, "name", "eth0", ns=ns)
    run("ip", "link", "set", "eth0", "address", mac(side, slot), ns=ns)
    run("ip", "link", "set", "lo", "up", ns=ns)
    return {**info, "mac": mac(side, slot)}


def slot_id(vlan, parent):
    if vlan is not None:
        if not 1 <= vlan <= 4094:
            raise ProbeError(f"VLAN {vlan} out of range")
        return vlan
    if not parent:
        raise ProbeError("need a VLAN or a parent")
    if not link_exists(parent):
        raise ProbeError(f"no interface {parent}")
    return SLOT_OFFSET + ifindex(parent)


def state_dir(slot):
    return RUN / str(slot)


def read_state(slot):
    try:
        return json.loads((state_dir(slot) / "state.json").read_text())
    except FileNotFoundError:
        raise ProbeError(f"slot {slot} is not up") from None


def write_state(slot, state):
    path = state_dir(slot)
    path.mkdir(parents=True, exist_ok=True)
    (path / "state.json").write_text(json.dumps(state, indent=1))


def sysctl(ns, key, value):
    run("sysctl", "-qw", f"{key}={value}", ns=ns)


# --- LAN side -------------------------------------------------------------------


def cmd_up(args, config):
    slot = slot_id(args.lan_vlan, args.parent if args.lan_vlan is None else None)
    ns = f"np-lan{slot}"
    cmd_down_slot(slot)
    state_dir(slot).mkdir(parents=True, exist_ok=True)
    run("ip", "netns", "add", ns)
    try:
        sysctl(ns, "net.ipv6.conf.default.accept_ra", 1)
        lan = attach(ns, 1, slot, args.lan_vlan, args.trunk or args.parent, config)
        # SLAAC from the DUT's RAs; nothing else runs here until a test does
        sysctl(ns, "net.ipv6.conf.eth0.accept_ra", 1)
        run("ip", "link", "set", "eth0", "up", ns=ns)
    except ProbeError:
        cmd_down_slot(slot)
        raise
    state = {"slot": slot, "lan_ns": ns, "lan": lan}
    write_state(slot, state)
    return state


# --- WAN side ------------------------------------------------------------------


def wan_plan(wan_id, delegated_len):
    site = ipaddress.ip_network(f"2001:db8:{wan_id:x}::/48")
    link = next(site.subnets(new_prefix=64))
    pd_pool = ipaddress.ip_network(f"2001:db8:{wan_id:x}:{PD_POOL_SUBNET:x}::/56")
    if not 56 < delegated_len <= 64:
        raise ProbeError(f"delegated length {delegated_len} not in 57..64")
    return {
        "site": str(site),
        "link": str(link),
        "router": str(link[1]),
        "pool": [str(link[0x100]), str(link[0x1FF])],
        "pd_pool": str(pd_pool),
        "delegated_len": delegated_len,
    }


def kea_config(plan, directory):
    pd_pool = ipaddress.ip_network(plan["pd_pool"])
    return {
        "Dhcp6": {
            "interfaces-config": {"interfaces": ["eth0"]},
            "lease-database": {
                "type": "memfile",
                "name": str(directory / "kea-leases6.csv"),
                "lfc-interval": 0,
            },
            # Short times, so a renewal shows within a test
            "renew-timer": 300,
            "rebind-timer": 480,
            "preferred-lifetime": 600,
            "valid-lifetime": 900,
            "subnet6": [
                {
                    "id": 1,
                    "subnet": plan["link"],
                    "interface": "eth0",
                    "pools": [{"pool": "-".join(plan["pool"])}],
                    "pd-pools": [
                        {
                            "prefix": str(pd_pool.network_address),
                            "prefix-len": pd_pool.prefixlen,
                            "delegated-len": plan["delegated_len"],
                        }
                    ],
                }
            ],
            "loggers": [
                {
                    "name": "kea-dhcp6",
                    "severity": "INFO",
                    "output-options": [{"output": str(directory / "kea-dhcp6.log")}],
                }
            ],
        }
    }


def radvd_config(plan):
    # M and O flags: addresses and prefixes come from DHCPv6; the RA gives the
    # DUT its default route and the on-link /64
    return f"""interface eth0 {{
  AdvSendAdvert on;
  MinRtrAdvInterval 3;
  MaxRtrAdvInterval 10;
  AdvManagedFlag on;
  AdvOtherConfigFlag on;
  prefix {plan["link"]} {{
    AdvOnLink on;
    AdvAutonomous off;
  }};
}};
"""


def start_daemon(name, ns, argv, directory, env=None):
    # the daemon keeps its own copy of the descriptor
    with open(directory / f"{name}.out", "ab") as log:
        proc = subprocess.Popen(
            ["ip", "netns", "exec", ns, *argv],
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env={**os.environ, **(env or {})},
            start_new_session=True,
        )
    (directory / f"{name}.pid").write_text(str(proc.pid))
    return proc


def wait_alive(name, proc, directory, settle=1.0):
    time.sleep(settle)
    if proc.poll() is not None:
        out = (directory / f"{name}.out").read_text(errors="replace")[-2000:]
        raise ProbeError(f"{name} exited ({proc.returncode}): {out}")


def cmd_wan_up(args, config):
    state = read_state(args.slot)
    slot = state["slot"]
    stop_daemons(slot)
    if state.get("wan_ns") in namespaces():
        run("ip", "netns", "del", state["wan_ns"])
    wan_id = slot_id(args.wan_vlan, args.wan_parent if args.wan_vlan is None else None)
    ns = f"np-wan{slot}"
    plan = wan_plan(wan_id, args.delegated_len)
    directory = state_dir(slot)
    for stale in directory.glob("kea-*"):
        stale.unlink()
    run("ip", "netns", "add", ns)
    try:
        sysctl(ns, "net.ipv6.conf.default.accept_ra", 0)
        wan = attach(ns, 2, slot, args.wan_vlan, args.trunk or args.wan_parent, config)
        sysctl(ns, "net.ipv6.conf.eth0.accept_ra", 0)
        # radvd warns without forwarding; the namespace routes nothing else
        sysctl(ns, "net.ipv6.conf.all.forwarding", 1)
        run(
            "ip",
            "-6",
            "addr",
            "add",
            f"{plan['router']}/64",
            "dev",
            "eth0",
            "nodad",
            ns=ns,
        )
        run("ip", "link", "set", "eth0", "up", ns=ns)

        conf = directory / "kea-dhcp6.json"
        conf.write_text(json.dumps(kea_config(plan, directory), indent=1))
        # Kea 3 only accepts these paths below its directories
        kea_env = {
            "KEA_DHCP_DATA_DIR": str(directory),
            "KEA_LOG_FILE_DIR": str(directory),
            "KEA_PIDFILE_DIR": str(directory),
            "KEA_LOCKFILE_DIR": str(directory),
        }
        kea = start_daemon(
            "kea-dhcp6", ns, ["kea-dhcp6", "-c", str(conf)], directory, kea_env
        )
        (directory / "radvd.conf").write_text(radvd_config(plan))
        radvd = start_daemon(
            "radvd",
            ns,
            [
                "radvd",
                "--nodaemon",
                "-C",
                str(directory / "radvd.conf"),
                "-p",
                str(directory / "radvd.daemon.pid"),
                "-m",
                "stderr",
            ],
            directory,
        )
        wait_alive("kea-dhcp6", kea, directory)
        wait_alive("radvd", radvd, directory, settle=0)
    except ProbeError:
        stop_daemons(slot)
        if ns in namespaces():
            run("ip", "netns", "del", ns)
        raise
    state.update({"wan_ns": ns, "wan": {**wan, "id": wan_id}, "ipv6": plan})
    write_state(slot, state)
    return state


# --- leases ----------------------------------------------------------------------

LEASE_TYPES = {"0": "na", "1": "ta", "2": "pd"}


def cmd_leases(args, config):
    state = read_state(args.slot)
    path = state_dir(state["slot"]) / "kea-leases6.csv"
    leases = {}
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                kind = LEASE_TYPES.get(row["lease_type"], row["lease_type"])
                prefix = (
                    f"{row['address']}/{row['prefix_len']}"
                    if kind == "pd"
                    else row["address"]
                )
                # The memfile appends: the last line for an address wins;
                # valid_lifetime 0 means released
                leases[(kind, prefix)] = {
                    "type": kind,
                    "address": prefix,
                    "duid": row["duid"],
                    "iaid": int(row["iaid"]),
                    "valid_lifetime": int(row["valid_lifetime"]),
                    "expire": int(row["expire"]),
                    "hostname": row.get("hostname", ""),
                    "state": int(row.get("state") or 0),
                }
    except FileNotFoundError:
        pass
    now = time.time()
    active = [
        lease
        for lease in leases.values()
        if lease["valid_lifetime"] > 0 and lease["expire"] > now and lease["state"] == 0
    ]
    return {"leases": active}


# --- teardown, info, exec ----------------------------------------------------------


def stop_daemons(slot):
    directory = state_dir(slot)
    for name in DAEMONS:
        pidfile = directory / f"{name}.pid"
        try:
            pid = int(pidfile.read_text())
        except (FileNotFoundError, ValueError):
            continue
        try:
            os.killpg(pid, signal.SIGTERM)
            for _ in range(30):
                os.killpg(pid, 0)
                time.sleep(0.1)
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        pidfile.unlink(missing_ok=True)


def cmd_down_slot(slot):
    stop_daemons(slot)
    existing = namespaces()
    for ns in (f"np-lan{slot}", f"np-wan{slot}"):
        if ns in existing:
            # deleting the namespace deletes its VLAN or macvlan device
            run("ip", "netns", "del", ns)
    shutil.rmtree(state_dir(slot), ignore_errors=True)
    return {"ok": True, "slot": slot}


def cmd_down(args, config):
    return cmd_down_slot(args.slot)


def cmd_info(args, config):
    if args.slot is None:
        slots = sorted(int(p.name) for p in RUN.glob("*") if p.name.isdigit())
        return {"slots": slots, "namespaces": sorted(namespaces())}
    state = read_state(args.slot)
    for side in ("lan", "wan"):
        ns = state.get(f"{side}_ns")
        if ns:
            state[f"{side}_addresses"] = ip_json("addr", "show", "dev", "eth0", ns=ns)
    state["daemons"] = {}
    for name in DAEMONS:
        try:
            pid = int((state_dir(args.slot) / f"{name}.pid").read_text())
            os.kill(pid, 0)
            state["daemons"][name] = pid
        except (FileNotFoundError, ValueError, ProcessLookupError):
            state["daemons"][name] = None
    return state


def cmd_exec(args, config):
    state = read_state(args.slot)
    ns = state.get(f"{args.side}_ns")
    if not ns:
        raise ProbeError(f"slot {args.slot} has no {args.side} side")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise ProbeError("no command")
    os.execvp("ip", ["ip", "netns", "exec", ns, *command])


def parser():
    p = argparse.ArgumentParser(prog="nixos-probe", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("up", help="create a slot's LAN side")
    up.add_argument("--lan-vlan", type=int, help="the DUT's LAN VLAN on the trunk")
    up.add_argument("--trunk", help="trunk port (default: the default route's)")
    up.add_argument(
        "--parent", help="without a VLAN: the interface towards the DUT's LAN"
    )
    up.set_defaults(func=cmd_up)

    wan = sub.add_parser(
        "wan-up", help="(re)create a slot's WAN side: radvd, kea-dhcp6"
    )
    wan.add_argument("--slot", type=int, required=True)
    wan.add_argument("--wan-vlan", type=int, help="the DUT's WAN VLAN on the trunk")
    wan.add_argument("--trunk", help="trunk port (default: the default route's)")
    wan.add_argument(
        "--wan-parent", help="without a VLAN: the interface towards the DUT's WAN"
    )
    wan.add_argument("--delegated-len", type=int, default=DEFAULT_DELEGATED_LEN)
    wan.set_defaults(func=cmd_wan_up)

    for name, func, text in (
        ("down", cmd_down, "remove a slot (also a half-built one)"),
        ("leases", cmd_leases, "kea-dhcp6's active leases (na, pd)"),
    ):
        s = sub.add_parser(name, help=text)
        s.add_argument("--slot", type=int, required=True)
        s.set_defaults(func=func)

    info = sub.add_parser("info", help="a slot's state, or the list of slots")
    info.add_argument("--slot", type=int)
    info.set_defaults(func=cmd_info)

    ex = sub.add_parser("exec", help="run a command in a slot's namespace")
    ex.add_argument("--slot", type=int, required=True)
    ex.add_argument("side", choices=("lan", "wan"))
    ex.add_argument("command", nargs=argparse.REMAINDER)
    ex.set_defaults(func=cmd_exec)
    return p


def main():
    args = parser().parse_args()
    if args.cmd == "up" and args.lan_vlan is None and not args.parent:
        print(json.dumps({"error": "up needs --lan-vlan or --parent"}))
        return 1
    if args.cmd == "wan-up" and args.wan_vlan is None and not args.wan_parent:
        print(json.dumps({"error": "wan-up needs --wan-vlan or --wan-parent"}))
        return 1
    try:
        print(json.dumps(args.func(args, load_config())))
    except ProbeError as e:
        print(json.dumps({"error": str(e)}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
