# SPDX-License-Identifier: LGPL-2.1-only
"""Config renderers for the WAN-side services. Pure functions, no I/O.

Each takes plain values and returns the file content (str), so they can be
unit-tested without a probe. The addressing defaults live in WanPlan.
"""

import ipaddress
import json
from dataclasses import dataclass

HEADER = "# managed by dprobe, rewritten on every start\n"


@dataclass(frozen=True)
class WanPlan:
    """Addresses of one slot's WAN segment. The same in every slot: the
    netns keeps them apart, so tests need no per-DUT constants."""

    net4: str = "172.16.100.0/24"
    net6: str = "2001:db8:100::/64"
    pd_pool: str = "2001:db8:200::/48"

    @property
    def iface4(self):
        """The probe's own address with prefix length (first host)."""
        net = ipaddress.ip_network(self.net4)
        return f"{net.network_address + 1}/{net.prefixlen}"

    @property
    def addr4(self):
        return str(ipaddress.ip_network(self.net4).network_address + 1)

    @property
    def iface6(self):
        net = ipaddress.ip_network(self.net6)
        return f"{net.network_address + 1}/{net.prefixlen}"

    @property
    def addr6(self):
        return str(ipaddress.ip_network(self.net6).network_address + 1)

    def default_range4(self):
        """.10 .. .99 of net4 (for a /24)."""
        net = ipaddress.ip_network(self.net4)
        return str(net.network_address + 10), str(net.network_address + 99)

    def na_pool6(self):
        net = ipaddress.ip_network(self.net6)
        return f"{net.network_address + 0x1000}-{net.network_address + 0x1FFF}"


def parse_dhcp_option(spec):
    """'28=172.16.100.127' or 'option:ntp-server=1.2.3.4' -> (key, value).

    The key is a number or a dnsmasq option name; the value is passed to
    dnsmasq as is (comma-separated lists allowed).
    """
    key, sep, value = spec.partition("=")
    key = key.strip()
    if not sep or not key or not value:
        raise ValueError(f"bad DHCP option {spec!r}: want CODE=VALUE")
    if key.isdigit():
        code = int(key)
        if not 1 <= code <= 254:
            raise ValueError(f"bad DHCP option code {code}")
        return str(code), value.strip()
    if not key.startswith("option:"):
        key = f"option:{key}"
    return key, value.strip()


def dnsmasq_dhcp4(
    iface,
    plan,
    leasefile,
    pidfile,
    range4=None,
    lease_time="12h",
    options=(),
    dns=False,
    hosts=(),
):
    """dnsmasq as the DUT WAN's DHCPv4 server (DNS off unless dns=True).

    options: (key, value) pairs from parse_dhcp_option(); sent with
    dhcp-option-force, i.e. even if the client didn't ask for them.
    hosts: (name, address) records served when dns=True.
    """
    net = ipaddress.ip_network(plan.net4)
    start, end = range4 or plan.default_range4()
    for a in (start, end):
        if ipaddress.ip_address(a) not in net:
            raise ValueError(f"DHCP range address {a} not in {net}")
    gw = plan.addr4
    lines = [
        HEADER.rstrip("\n"),
        f"interface={iface}",
        "bind-interfaces",
        "except-interface=lo",
        "user=root",
        f"pid-file={pidfile}",
        f"dhcp-leasefile={leasefile}",
        "dhcp-authoritative",
        "log-dhcp",
        "no-resolv",
        "no-hosts",
        f"dhcp-range={start},{end},{net.netmask},{lease_time}",
        f"dhcp-option=option:router,{gw}",
        f"dhcp-option=option:dns-server,{gw}",
        f"dhcp-option=option:ntp-server,{gw}",
    ]
    if dns:
        lines.append("port=53")
        lines.append(f"listen-address={gw}")
        lines += [f"host-record={name},{addr}" for name, addr in hosts]
    else:
        lines.append("port=0")
    lines += [f"dhcp-option-force={key},{value}" for key, value in options]
    return "\n".join(lines) + "\n"


def kea_dhcp6(
    iface,
    plan,
    leasefile,
    pd_len=60,
    na=True,
    pd=True,
    t1=None,
    t2=None,
    preferred=1800,
    valid=3600,
    rapid_commit=False,
):
    """kea-dhcp6 config (JSON text) with IA_NA and/or IA_PD.

    Short lifetimes (and t1/t2) are what the PD-expiry tests turn down.
    """
    pd_net = ipaddress.ip_network(plan.pd_pool)
    if not pd_net.prefixlen < pd_len <= 64:
        raise ValueError(f"pd_len {pd_len} must be in ({pd_net.prefixlen}, 64]")
    if preferred > valid:
        raise ValueError(f"preferred {preferred} > valid {valid}")
    t1 = t1 if t1 is not None else preferred // 2
    t2 = t2 if t2 is not None else preferred * 4 // 5
    if not t1 <= t2 <= preferred:
        raise ValueError(f"need t1 <= t2 <= preferred, got {t1}, {t2}, {preferred}")
    subnet = {
        "id": 1,
        "subnet": plan.net6,
        "interface": iface,
        "rapid-commit": rapid_commit,
    }
    if na:
        subnet["pools"] = [{"pool": plan.na_pool6()}]
    if pd:
        subnet["pd-pools"] = [
            {
                "prefix": str(pd_net.network_address),
                "prefix-len": pd_net.prefixlen,
                "delegated-len": pd_len,
            }
        ]
    conf = {
        "Dhcp6": {
            "interfaces-config": {
                "interfaces": [iface],
                "service-sockets-max-retries": 20,
                "service-sockets-retry-wait-time": 500,
            },
            "lease-database": {
                "type": "memfile",
                "persist": True,
                "name": str(leasefile),
                "lfc-interval": 0,
            },
            "calculate-tee-times": False,
            "renew-timer": t1,
            "rebind-timer": t2,
            "preferred-lifetime": preferred,
            "valid-lifetime": valid,
            "option-data": [{"name": "dns-servers", "data": plan.addr6}],
            "subnet6": [subnet],
            "loggers": [
                {
                    "name": "kea-dhcp6",
                    "output-options": [{"output": "stdout"}],
                    "severity": "INFO",
                }
            ],
        }
    }
    return json.dumps(conf, indent=2) + "\n"


def radvd(
    iface,
    plan,
    managed=True,
    other=True,
    autonomous=True,
    router_lifetime=600,
    min_interval=5,
    max_interval=15,
    rdnss=True,
):
    """radvd announcing plan.net6 on the WAN segment."""
    if not 3 <= min_interval <= 0.75 * max_interval:
        raise ValueError("need 3 <= min_interval <= 0.75 * max_interval")
    if router_lifetime and not max_interval <= router_lifetime <= 9000:
        raise ValueError("router_lifetime must be 0 or in [max_interval, 9000]")
    onoff = {True: "on", False: "off"}
    lines = [
        HEADER.rstrip("\n"),
        f"interface {iface} {{",
        "    AdvSendAdvert on;",
        f"    MinRtrAdvInterval {min_interval};",
        f"    MaxRtrAdvInterval {max_interval};",
        f"    AdvDefaultLifetime {router_lifetime};",
        f"    AdvManagedFlag {onoff[managed]};",
        f"    AdvOtherConfigFlag {onoff[other]};",
        f"    prefix {plan.net6} {{",
        "        AdvOnLink on;",
        f"        AdvAutonomous {onoff[autonomous]};",
        "    };",
    ]
    if rdnss:
        lines += [f"    RDNSS {plan.addr6} {{", "    };"]
    lines.append("};")
    return "\n".join(lines) + "\n"


def chrony_server(bind4, pidfile):
    """chronyd serving the probe's clock (it never steers it: run with -x)."""
    return (
        HEADER
        + f"pidfile {pidfile}\n"
        + "cmdport 0\n"
        + "bindcmdaddress /\n"
        + f"bindaddress {bind4}\n"
        + "local stratum 8\n"
        + "allow all\n"
    )
