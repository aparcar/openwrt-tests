# SPDX-License-Identifier: LGPL-2.1-only
"""Slots: one per DUT, keyed by the DUT's LAN VLAN (like the OpenWrt probe).

A slot is two network namespaces on VLANs of the trunk:

  dp-lan<N>  VLAN dev dpl<N> (DUT LAN): the probe as a LAN client
  dp-wan<N>  VLAN dev dpw<N> (DUT WAN): the probe as the DUT's upstream,
             with the WanPlan addresses (172.16.100.1, 2001:db8:100::1)

Namespaces rather than VRFs: every slot can use the same addresses, each
namespace has its own conntrack table and sysctls (so no conntrack zones are
needed), and daemons need no VRF awareness. Nothing is configured per DUT
on the probe: callers pass the VLANs, which come from the DUT's place.
"""

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field

from dprobe import services, wan
from dprobe.render import WanPlan
from dprobe.util import (
    RUN_DIR,
    ProbeError,
    ip_json,
    load_config,
    mac_for,
    run,
    vlan_id,
    write_file,
)

LAN_KIND, WAN_KIND = 0x00, 0x01


@dataclass
class Slot:
    lan_vlan: int
    trunk: str
    wan_vlan: int | None = None
    net4: str = WanPlan.net4
    net6: str = WanPlan.net6
    pd_pool: str = WanPlan.pd_pool
    created: float = field(default_factory=time.time)

    @property
    def lan_ns(self):
        return f"dp-lan{self.lan_vlan}"

    @property
    def lan_dev(self):
        return f"dpl{self.lan_vlan}"

    @property
    def lan_mac(self):
        return mac_for(LAN_KIND, self.lan_vlan)

    @property
    def wan_ns(self):
        return f"dp-wan{self.lan_vlan}"

    @property
    def wan_dev(self):
        return f"dpw{self.lan_vlan}"

    @property
    def wan_mac(self):
        return mac_for(WAN_KIND, self.wan_vlan or 0)

    @property
    def plan(self):
        return WanPlan(self.net4, self.net6, self.pd_pool)

    @property
    def dir(self):
        return RUN_DIR / str(self.lan_vlan)

    def side(self, name):
        """(netns, device) of the 'lan' or 'wan' side."""
        if name == "lan":
            return self.lan_ns, self.lan_dev
        if name == "wan":
            if self.wan_vlan is None:
                raise ProbeError(
                    f"slot {self.lan_vlan} has no WAN side (slot up --wan-vlan)"
                )
            return self.wan_ns, self.wan_dev
        raise ProbeError(f"unknown side {name!r}")

    def describe(self):
        d = asdict(self)
        d.update(
            lan_ns=self.lan_ns,
            lan_dev=self.lan_dev,
            lan_mac=self.lan_mac,
            hostname=f"dprobe{self.lan_vlan}",
        )
        if self.wan_vlan is not None:
            plan = self.plan
            d.update(
                wan_ns=self.wan_ns,
                wan_dev=self.wan_dev,
                wan_mac=self.wan_mac,
                wan_ip=plan.addr4,
                wan_ip6=plan.addr6,
            )
        return d

    def save(self):
        write_file(self.dir / "slot.json", json.dumps(asdict(self)) + "\n")


def parse_vlan_list(spec):
    """[101, "110-112"] or "101,110-112" -> {101, 110, 111, 112}."""
    if isinstance(spec, str):
        spec = [s for s in spec.split(",") if s.strip()]
    out = set()
    for item in spec or []:
        if isinstance(item, int):
            out.add(item)
            continue
        lo, _, hi = str(item).partition("-")
        out.update(range(int(lo), int(hi or lo) + 1))
    return out


def check_vlan(config, vid):
    if vid == config.get("mgmt_vlan"):
        raise ProbeError(f"VLAN {vid} is the probe's management VLAN")
    allowed = parse_vlan_list(config.get("allowed_vlans"))
    if allowed and vid not in allowed:
        raise ProbeError(f"VLAN {vid} not in allowed_vlans of the probe config")


def default_trunk(config):
    if config.get("trunk"):
        return config["trunk"]
    for route in ip_json("route", "show", "default"):
        if route.get("dev"):
            return route["dev"]
    raise ProbeError("no trunk: set 'trunk' in the probe config")


def load(lan_vlan):
    lan_vlan = vlan_id(lan_vlan)
    path = RUN_DIR / str(lan_vlan) / "slot.json"
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise ProbeError(f"slot {lan_vlan} is not up", not_up=True) from None
    return Slot(**data)


def ns_exists(name):
    return os.path.exists(f"/run/netns/{name}")


def _ns_sysctl(ns, **settings):
    args = [f"{k.replace('__', '.')}={v}" for k, v in settings.items()]
    run(["ip", "netns", "exec", ns, "sysctl", "-q", "-w", *args])


def _make_side(ns, dev, trunk, vlan, mac, router):
    run(["ip", "netns", "add", ns])
    # before the device moves in: it inherits the namespace's defaults
    _ns_sysctl(
        ns,
        net__ipv6__conf__default__accept_dad=0,
        net__ipv6__conf__all__accept_dad=0,
        net__ipv6__conf__default__use_tempaddr=0,
        net__ipv6__conf__all__forwarding=1 if router else 0,
        net__ipv4__ip_forward=1 if router else 0,
    )
    run(["ip", "link", "add", "link", trunk, "name", dev, "type", "vlan", "id", vlan])
    run(["ip", "link", "set", dev, "address", mac, "netns", ns])
    run(["ip", "-n", ns, "link", "set", "lo", "up"])
    run(["ip", "-n", ns, "link", "set", dev, "up"])


def _wait_link_local(ns, dev, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for link in ip_json("-6", "addr", "show", "dev", dev, netns=ns):
            for a in link.get("addr_info", []):
                if a.get("scope") == "link" and "tentative" not in a:
                    return a["local"]
        time.sleep(0.1)
    raise ProbeError(f"{dev} in {ns} got no link-local address")


def down(lan_vlan):
    lan_vlan = vlan_id(lan_vlan)
    services.stop_all(lan_vlan)
    try:
        wan.cleanup_files(load(lan_vlan))
    except ProbeError:
        pass  # no state (never up, or half-built)
    for ns in (f"dp-lan{lan_vlan}", f"dp-wan{lan_vlan}"):
        if ns_exists(ns):
            run(["ip", "netns", "del", ns])  # takes its VLAN device along
    shutil.rmtree(RUN_DIR / str(lan_vlan), ignore_errors=True)
    return {"ok": True, "lan_vlan": lan_vlan}


def up(lan_vlan, wan_vlan=None, trunk=None, net4=None, net6=None, pd_pool=None):
    config = load_config()
    lan_vlan = vlan_id(lan_vlan)
    check_vlan(config, lan_vlan)
    if wan_vlan is not None:
        wan_vlan = vlan_id(wan_vlan)
        check_vlan(config, wan_vlan)
        if wan_vlan == lan_vlan:
            raise ProbeError("wan_vlan must differ from lan_vlan")
    trunk = trunk or default_trunk(config)
    if not os.path.exists(f"/sys/class/net/{trunk}"):
        raise ProbeError(f"trunk {trunk} does not exist")

    down(lan_vlan)
    slot = Slot(lan_vlan=lan_vlan, trunk=trunk, wan_vlan=wan_vlan)
    if net4:
        slot.net4 = net4
    if net6:
        slot.net6 = net6
    if pd_pool:
        slot.pd_pool = pd_pool
    try:
        _make_side(
            slot.lan_ns, slot.lan_dev, trunk, lan_vlan, slot.lan_mac, router=False
        )
        if wan_vlan is not None:
            _make_side(
                slot.wan_ns, slot.wan_dev, trunk, wan_vlan, slot.wan_mac, router=True
            )
            plan = slot.plan
            run(
                [
                    "ip",
                    "-n",
                    slot.wan_ns,
                    "addr",
                    "add",
                    plan.iface4,
                    "dev",
                    slot.wan_dev,
                ]
            )
            run(
                [
                    "ip",
                    "-n",
                    slot.wan_ns,
                    "-6",
                    "addr",
                    "add",
                    plan.iface6,
                    "dev",
                    slot.wan_dev,
                    "nodad",
                ]
            )
        lan_ll = _wait_link_local(slot.lan_ns, slot.lan_dev)
        wan_ll = _wait_link_local(slot.wan_ns, slot.wan_dev) if wan_vlan else None
        slot.save()
    except ProbeError:
        down(lan_vlan)
        raise
    return {**slot.describe(), "lan_ll": lan_ll, "wan_ll": wan_ll}


def _link_stats(ns, dev):
    links = ip_json("-s", "link", "show", "dev", dev, netns=ns)
    if not links:
        return None
    stats = links[0].get("stats64") or links[0].get("stats") or {}
    return {
        "operstate": links[0].get("operstate"),
        "rx_packets": stats.get("rx", {}).get("packets"),
        "tx_packets": stats.get("tx", {}).get("packets"),
    }


def addresses(ns, dev):
    out = {"ipv4": [], "ipv6": []}
    for link in ip_json("addr", "show", "dev", dev, netns=ns):
        for a in link.get("addr_info", []):
            entry = {
                "address": a["local"],
                "prefixlen": a["prefixlen"],
                "scope": a.get("scope"),
                "dynamic": bool(a.get("dynamic")),
            }
            if a["family"] == "inet":
                entry["broadcast"] = a.get("broadcast")
                out["ipv4"].append(entry)
            elif a["family"] == "inet6":
                entry["valid"] = a.get("valid_life_time")
                entry["preferred"] = a.get("preferred_life_time")
                out["ipv6"].append(entry)
    return out


def info(lan_vlan):
    slot = load(lan_vlan)
    d = slot.describe()
    d["lan"] = {
        "ns_up": ns_exists(slot.lan_ns),
        "link": _link_stats(slot.lan_ns, slot.lan_dev)
        if ns_exists(slot.lan_ns)
        else None,
    }
    if slot.wan_vlan is not None:
        d["wan"] = {
            "ns_up": ns_exists(slot.wan_ns),
            "link": _link_stats(slot.wan_ns, slot.wan_dev)
            if ns_exists(slot.wan_ns)
            else None,
        }
    d["services"] = services.status_all(slot.lan_vlan)
    return d


def list_slots():
    if not RUN_DIR.exists():
        return []
    return sorted(int(p.name) for p in RUN_DIR.iterdir() if (p / "slot.json").exists())
