# SPDX-License-Identifier: LGPL-2.1-only
"""WAN-side servers of a slot: DHCPv4 (dnsmasq), DHCPv6 (kea), RA (radvd),
NTP (chrony). Each start renders a fresh config and (re)starts the unit.

Paths: kea and chrony get their files in their own directories, which is
where Debian's AppArmor profiles for them allow access; dnsmasq and radvd
use the slot's run directory.
"""

from pathlib import Path

from dprobe import leases, render, services
from dprobe.util import ProbeError, load_config, write_file

SERVICES = ("dhcp4", "dhcp6", "ra", "ntp")


def _paths(slot):
    config = load_config()
    kea_conf = Path(config.get("kea_conf_dir", "/etc/kea"))
    kea_data = Path(config.get("kea_data_dir", "/var/lib/kea"))
    chrony_conf = Path(config.get("chrony_conf_dir", "/etc/chrony"))
    chrony_run = Path(config.get("chrony_run_dir", "/run/chrony"))
    v = slot.lan_vlan
    return {
        "dnsmasq_conf": slot.dir / "dnsmasq.conf",
        "dnsmasq_leases": slot.dir / "dnsmasq.leases",
        "dnsmasq_pid": slot.dir / "dnsmasq.pid",
        "kea_conf": kea_conf / f"dprobe-{v}-dhcp6.json",
        "kea_leases": kea_data / f"dprobe-{v}-leases6.csv",
        "radvd_conf": slot.dir / "radvd.conf",
        "radvd_pid": slot.dir / "radvd.pid",
        "chrony_conf": chrony_conf / f"dprobe-{v}.conf",
        "chrony_pid": chrony_run / f"dprobe-{v}.pid",
    }


def _wan(slot):
    return slot.side("wan")


def start_dhcp4(slot, range4=None, lease_time="12h", options=(), dns=False, hosts=()):
    ns, dev = _wan(slot)
    p = _paths(slot)
    try:
        parsed = [render.parse_dhcp_option(o) for o in options]
        host_records = []
        for h in hosts:
            name, _, addr = h.partition("=")
            if not name or not addr:
                raise ValueError(f"bad host record {h!r}: want NAME=ADDRESS")
            host_records.append((name, addr))
        conf = render.dnsmasq_dhcp4(
            dev,
            slot.plan,
            p["dnsmasq_leases"],
            p["dnsmasq_pid"],
            range4=range4,
            lease_time=lease_time,
            options=parsed,
            dns=dns,
            hosts=host_records,
        )
    except ValueError as e:
        raise ProbeError(str(e)) from e
    write_file(p["dnsmasq_conf"], conf)
    p["dnsmasq_leases"].unlink(missing_ok=True)
    p["dnsmasq_leases"].touch()
    st = services.start(
        slot.lan_vlan, "dhcp4", ns, ["dnsmasq", "-k", "-C", p["dnsmasq_conf"]]
    )
    return {"service": "dhcp4", "status": st, "config": conf.splitlines()}


def start_dhcp6(slot, keep_leases=False, **kwargs):
    ns, dev = _wan(slot)
    p = _paths(slot)
    try:
        conf = render.kea_dhcp6(dev, slot.plan, p["kea_leases"], **kwargs)
    except ValueError as e:
        raise ProbeError(str(e)) from e
    write_file(p["kea_conf"], conf)
    if not keep_leases:
        services.stop(slot.lan_vlan, "dhcp6")
        for f in p["kea_leases"].parent.glob(p["kea_leases"].name + "*"):
            f.unlink(missing_ok=True)
    st = services.start(slot.lan_vlan, "dhcp6", ns, ["kea-dhcp6", "-c", p["kea_conf"]])
    return {"service": "dhcp6", "status": st, "config": conf}


def start_ra(slot, **kwargs):
    ns, dev = _wan(slot)
    p = _paths(slot)
    try:
        conf = render.radvd(dev, slot.plan, **kwargs)
    except ValueError as e:
        raise ProbeError(str(e)) from e
    write_file(p["radvd_conf"], conf)
    st = services.start(
        slot.lan_vlan,
        "ra",
        ns,
        [
            "radvd",
            "--nodaemon",
            "--config",
            p["radvd_conf"],
            "--pidfile",
            p["radvd_pid"],
            "--logmethod",
            "stderr",
        ],
    )
    return {"service": "ra", "status": st, "config": conf.splitlines()}


def start_ntp(slot):
    ns, _dev = _wan(slot)
    p = _paths(slot)
    conf = render.chrony_server(slot.plan.addr4, p["chrony_pid"])
    write_file(p["chrony_conf"], conf)
    # -d: foreground, log to stderr; -x: never touch the (shared) clock
    st = services.start(
        slot.lan_vlan, "ntp", ns, ["chronyd", "-d", "-x", "-f", p["chrony_conf"]]
    )
    return {"service": "ntp", "status": st, "server": slot.plan.addr4}


def stop(slot, name):
    names = SERVICES if name == "all" else (name,)
    for n in names:
        services.stop(slot.lan_vlan, n)
    return {"stopped": list(names)}


def lease_list(slot):
    p = _paths(slot)

    def read(path):
        try:
            return path.read_text()
        except FileNotFoundError:
            return ""

    return {
        "dhcp4": leases.dnsmasq(read(p["dnsmasq_leases"])),
        "dhcp6": leases.kea6(read(p["kea_leases"])),
    }


def cleanup_files(slot):
    """Remove the files outside the slot dir (kea, chrony)."""
    p = _paths(slot)
    for key in ("kea_conf", "chrony_conf", "chrony_pid"):
        p[key].unlink(missing_ok=True)
    for f in p["kea_leases"].parent.glob(p["kea_leases"].name + "*"):
        f.unlink(missing_ok=True)
