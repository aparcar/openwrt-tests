# SPDX-License-Identifier: LGPL-2.1-only
"""Command line: `dprobe <command> ...`, one JSON document on stdout.

Exit codes (util.OK/FAILED/ERROR): 0 ok, 1 ran but the expected thing
didn't happen (no lease, no RA, unanswered DNS), 2 error. The driver
(drivers/debianprobedriver.py) maps them; keep the two in sync.

Every slot command takes the DUT's LAN VLAN first. LAN-side clients run
inside the slot's namespace (setns in this process), WAN-side servers run
as transient systemd units (services.py).
"""

import argparse
import json
import logging
import os
import re
import sys

from dprobe import __version__, slot, wan
from dprobe import services as svc
from dprobe.util import ERROR, FAILED, OK, ProbeError, ProbeFailed, run

# --- helpers ------------------------------------------------------------------


def enter_netns(name):
    """Move this process into network namespace `name` (like ip netns exec,
    minus the /sys remount, which nothing here needs)."""
    path = f"/run/netns/{name}"
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        raise ProbeError(f"namespace {name} does not exist") from None
    try:
        os.setns(fd, os.CLONE_NEWNET)
    finally:
        os.close(fd)


def _side(args):
    s = slot.load(args.lan_vlan)
    return s, *s.side(args.side)


PING_RE = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received")
RTT_RE = re.compile(r"= ([\d.]+)/([\d.]+)/([\d.]+)/")


def parse_ping(out):
    """iputils ping output -> {transmitted, received, rtt_min/avg/max}."""
    m = PING_RE.search(out)
    result = {
        "transmitted": int(m.group(1)) if m else 0,
        "received": int(m.group(2)) if m else 0,
    }
    m = RTT_RE.search(out)
    if m:
        result.update(
            rtt_min=float(m.group(1)),
            rtt_avg=float(m.group(2)),
            rtt_max=float(m.group(3)),
        )
    return result


def _range(value):
    start, sep, end = value.partition(",")
    if not sep:
        raise argparse.ArgumentTypeError("want START,END")
    return start.strip(), end.strip()


# --- slot ---------------------------------------------------------------------


def cmd_slot_up(args):
    return slot.up(
        args.lan_vlan,
        wan_vlan=args.wan_vlan,
        trunk=args.trunk,
        net4=args.net4,
        net6=args.net6,
        pd_pool=args.pd_pool,
    )


def cmd_slot_down(args):
    return slot.down(args.lan_vlan)


def cmd_slot_info(args):
    return slot.info(args.lan_vlan)


def cmd_slot_list(args):
    return {"slots": slot.list_slots()}


def cmd_addresses(args):
    _s, ns, dev = _side(args)
    return {"side": args.side, "dev": dev, **slot.addresses(ns, dev)}


def cmd_ping(args):
    _s, ns, _dev = _side(args)
    argv = ["ip", "netns", "exec", ns, "ping", "-n", "-c", args.count, "-W", args.wait]
    if ":" in args.host:
        argv.insert(5, "-6")
    proc = run(
        argv + [args.host], check=False, timeout=args.count * (args.wait + 1) + 10
    )
    reply = {"host": args.host, "rc": proc.returncode, **parse_ping(proc.stdout)}
    reply["out"] = proc.stdout.strip().splitlines()[-4:]
    if reply["received"] == 0:
        raise ProbeFailed(reply)
    return reply


def cmd_logs(args):
    slot.load(args.lan_vlan)
    return {
        "service": args.service,
        "lines": svc.logs(args.lan_vlan, args.service, args.lines),
    }


# --- WAN servers --------------------------------------------------------------


def cmd_wan_dhcp4(args):
    return wan.start_dhcp4(
        slot.load(args.lan_vlan),
        range4=args.range,
        lease_time=args.lease_time,
        options=args.option or (),
        dns=args.dns,
        hosts=args.host or (),
    )


def cmd_wan_dhcp6(args):
    return wan.start_dhcp6(
        slot.load(args.lan_vlan),
        keep_leases=args.keep_leases,
        pd_len=args.pd_len,
        na=args.na,
        pd=args.pd,
        t1=args.t1,
        t2=args.t2,
        preferred=args.preferred,
        valid=args.valid,
        rapid_commit=args.rapid_commit,
    )


def cmd_wan_ra(args):
    return wan.start_ra(
        slot.load(args.lan_vlan),
        managed=args.managed,
        other=args.other,
        autonomous=args.autonomous,
        router_lifetime=args.router_lifetime,
        min_interval=args.min_interval,
        max_interval=args.max_interval,
        rdnss=args.rdnss,
    )


def cmd_wan_ntp(args):
    return wan.start_ntp(slot.load(args.lan_vlan))


def cmd_wan_stop(args):
    return wan.stop(slot.load(args.lan_vlan), args.service)


def cmd_wan_leases(args):
    return wan.lease_list(slot.load(args.lan_vlan))


# --- clients (inside the namespace) --------------------------------------------


def cmd_ra_capture(args):
    from dprobe import ra

    s, ns, dev = _side(args)
    mac = s.lan_mac if args.side == "lan" else s.wan_mac
    enter_netns(ns)
    ras = ra.capture(dev, args.duration, args.count, solicit=args.solicit, mac=mac)
    reply = {"dev": dev, "count": len(ras), "ras": ras}
    if not [r for r in ras if "error" not in r]:
        raise ProbeFailed({**reply, "error": f"no RA on {dev} in {args.duration}s"})
    return reply


def _apply_lease4(ns, dev, lease):
    run(["ip", "-n", ns, "-4", "addr", "flush", "dev", dev])
    run(
        [
            "ip",
            "-n",
            ns,
            "addr",
            "add",
            f"{lease['ip']}/{lease['prefixlen']}",
            "dev",
            dev,
        ]
    )
    if lease["router"]:
        run(["ip", "-n", ns, "route", "replace", "default", "via", lease["router"][0]])


def cmd_dhcp4(args):
    from dprobe import dhcp4

    s, ns, dev = _side(args)
    own = s.lan_mac if args.side == "lan" else s.wan_mac
    mac = (args.mac or own).lower()
    if args.apply and mac != own:
        raise ProbeError("--apply only with the slot's own MAC")
    enter_netns(ns)
    reply = dhcp4.exchange(
        dev,
        mac,
        timeout=args.timeout,
        request=args.request,
        server=args.server,
        hostname=args.hostname or s.describe()["hostname"],
        params=args.params,
    )
    if reply["lease"] is None:
        raise ProbeFailed({**reply, "error": "no lease"})
    if args.apply:
        _apply_lease4(ns, dev, reply["lease"])
        reply["applied"] = True
    return reply


def cmd_dhcp6(args):
    from dprobe import dhcp6

    s, ns, dev = _side(args)
    mac = (args.mac or (s.lan_mac if args.side == "lan" else s.wan_mac)).lower()
    enter_netns(ns)
    reply = dhcp6.exchange(
        dev,
        mac,
        na=args.na,
        pd=args.pd,
        pd_len=args.pd_len,
        request=args.request,
        timeout=args.timeout,
    )
    got = reply["reply"] if args.request else (reply["advertises"] or None)
    if not got:
        raise ProbeFailed({**reply, "error": "no DHCPv6 answer"})
    return reply


def cmd_dns_pair(args):
    from dprobe import dnspair

    _s, ns, _dev = _side(args)
    enter_netns(ns)
    reply = dnspair.run_pairs(
        args.server, args.name, tries=args.tries, timeout=args.timeout, gap=args.gap
    )
    if reply["answered"] != reply["tries"]:
        raise ProbeFailed(
            {
                **reply,
                "error": f"{reply['tries'] - reply['answered']} pairs not fully answered",
            }
        )
    return reply


# --- parser -------------------------------------------------------------------


def _bool_pair(p, name, default, help_):
    """--name / --no-name with a default."""
    p.add_argument(
        f"--{name}",
        dest=name.replace("-", "_"),
        action=argparse.BooleanOptionalAction,
        default=default,
        help=help_,
    )


def build_parser():
    parser = argparse.ArgumentParser(prog="dprobe", description=__doc__.split("\n")[0])
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="debug log on stderr"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name, func, help_, lan=True, side=False, parent=sub):
        p = parent.add_parser(name, help=help_)
        if lan:
            p.add_argument("lan_vlan", type=int, help="the DUT's LAN VLAN (the slot)")
        if side:
            p.add_argument("--side", choices=("lan", "wan"), default="lan")
        p.set_defaults(func=func)
        return p

    # slot
    sp = sub.add_parser("slot", help="create, inspect, remove a DUT's slot")
    ssub = sp.add_subparsers(dest="slot_command", required=True)
    p = command("up", cmd_slot_up, "(re)create the slot namespaces", parent=ssub)
    p.add_argument(
        "--wan-vlan", type=int, help="the DUT's WAN VLAN (adds the WAN side)"
    )
    p.add_argument("--trunk", help="interface carrying the VLANs")
    p.add_argument("--net4", help="WAN IPv4 subnet (default 172.16.100.0/24)")
    p.add_argument("--net6", help="WAN IPv6 subnet (default 2001:db8:100::/64)")
    p.add_argument("--pd-pool", help="DHCPv6-PD pool (default 2001:db8:200::/48)")
    command("down", cmd_slot_down, "stop services, remove namespaces", parent=ssub)
    command("info", cmd_slot_info, "slot, links and services", parent=ssub)
    command("list", cmd_slot_list, "LAN VLANs with a slot", lan=False, parent=ssub)

    command("addresses", cmd_addresses, "addresses of the slot device", side=True)
    p = command(
        "ping",
        cmd_ping,
        "ping from the namespace (fails if nothing comes back)",
        side=True,
    )
    p.add_argument("host")
    p.add_argument("-c", "--count", type=int, default=3)
    p.add_argument("-W", "--wait", type=int, default=2)
    p = command("logs", cmd_logs, "journal of a slot service")
    p.add_argument("service")
    p.add_argument("-n", "--lines", type=int, default=50)

    # WAN servers
    wp = sub.add_parser("wan", help="servers on the DUT's WAN segment")
    wsub = wp.add_subparsers(dest="wan_command", required=True)
    p = command(
        "dhcp4", cmd_wan_dhcp4, "(re)start the DHCPv4 server (dnsmasq)", parent=wsub
    )
    p.add_argument("--range", type=_range, help="START,END (default .10-.99)")
    p.add_argument("--lease-time", default="12h")
    p.add_argument(
        "--option",
        action="append",
        metavar="CODE=VALUE",
        help="extra option, forced into every reply (repeatable)",
    )
    p.add_argument(
        "--dns", action="store_true", help="also answer DNS on the WAN address"
    )
    p.add_argument(
        "--host",
        action="append",
        metavar="NAME=ADDRESS",
        help="DNS record with --dns (repeatable)",
    )
    p = command(
        "dhcp6", cmd_wan_dhcp6, "(re)start the DHCPv6 server (kea)", parent=wsub
    )
    _bool_pair(p, "na", True, "serve IA_NA")
    _bool_pair(p, "pd", True, "serve IA_PD")
    p.add_argument("--pd-len", type=int, default=60)
    p.add_argument("--t1", type=int)
    p.add_argument("--t2", type=int)
    p.add_argument("--preferred", type=int, default=1800)
    p.add_argument("--valid", type=int, default=3600)
    p.add_argument("--rapid-commit", action="store_true")
    p.add_argument(
        "--keep-leases",
        action="store_true",
        help="keep the lease file (default: start with no bindings)",
    )
    p = command(
        "ra", cmd_wan_ra, "(re)start router advertisements (radvd)", parent=wsub
    )
    _bool_pair(p, "managed", True, "M flag")
    _bool_pair(p, "other", True, "O flag")
    _bool_pair(p, "autonomous", True, "A flag of the prefix")
    _bool_pair(p, "rdnss", True, "RDNSS option")
    p.add_argument("--router-lifetime", type=int, default=600)
    p.add_argument("--min-interval", type=int, default=5)
    p.add_argument("--max-interval", type=int, default=15)
    command("ntp", cmd_wan_ntp, "(re)start the NTP server (chrony)", parent=wsub)
    p = command("stop", cmd_wan_stop, "stop a WAN server", parent=wsub)
    p.add_argument("service", choices=(*wan.SERVICES, "all"))
    command("leases", cmd_wan_leases, "current DHCPv4/v6 leases", parent=wsub)

    # clients
    p = command("ra-capture", cmd_ra_capture, "collect and decode RAs", side=True)
    p.add_argument("-d", "--duration", type=float, default=10.0)
    p.add_argument("-c", "--count", type=int, help="stop after this many RAs")
    _bool_pair(p, "solicit", True, "send a Router Solicitation first")
    p = command("dhcp4", cmd_dhcp4, "DHCPv4 DISCOVER/REQUEST", side=True)
    p.add_argument("--mac", help="client MAC (default: the slot's)")
    p.add_argument("--server", help="only accept offers with this server id")
    p.add_argument("--hostname")
    p.add_argument(
        "--params",
        type=lambda v: [int(x) for x in v.split(",")],
        help="parameter request list, e.g. 1,3,6,28",
    )
    p.add_argument("--timeout", type=float, default=10.0)
    _bool_pair(p, "request", True, "REQUEST the offer (else only DISCOVER)")
    p.add_argument(
        "--apply",
        action="store_true",
        help="configure the lease (address, default route) on the slot device",
    )
    p = command("dhcp6", cmd_dhcp6, "DHCPv6 SOLICIT/REQUEST", side=True)
    p.add_argument("--mac", help="DUID-LL MAC (default: the slot's)")
    _bool_pair(p, "na", True, "ask for IA_NA")
    _bool_pair(p, "pd", True, "ask for IA_PD")
    p.add_argument("--pd-len", type=int, help="prefix length hint")
    p.add_argument("--timeout", type=float, default=10.0)
    _bool_pair(p, "request", True, "REQUEST the best ADVERTISE")
    p = command(
        "dns-pair", cmd_dns_pair, "A+AAAA back to back, same 5-tuple", side=True
    )
    p.add_argument("server")
    p.add_argument("name")
    p.add_argument("--tries", type=int, default=10)
    p.add_argument("--timeout", type=float, default=2.0)
    p.add_argument("--gap", type=float, default=0.2)
    return parser


def main(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:  # argparse: usage on stderr; normalise the code
        if e.code in (0, None):
            return OK
        print(json.dumps({"error": "bad arguments"}))
        return ERROR
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        stream=sys.stderr,
        format="dprobe: %(message)s",
    )
    code = OK
    try:
        reply = args.func(args)
    except ProbeFailed as e:
        reply, code = e.reply, FAILED
    except ProbeError as e:
        reply, code = {"error": str(e), **e.extra}, ERROR
    except (OSError, ValueError) as e:
        reply, code = {"error": f"{type(e).__name__}: {e}"}, ERROR
    print(json.dumps(reply, default=str))
    return code
