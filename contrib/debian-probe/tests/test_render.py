# SPDX-License-Identifier: LGPL-2.1-only
import json

import pytest
from dprobe import render
from dprobe.render import WanPlan


def test_wan_plan_defaults():
    plan = WanPlan()
    assert plan.iface4 == "172.16.100.1/24"
    assert plan.addr4 == "172.16.100.1"
    assert plan.iface6 == "2001:db8:100::1/64"
    assert plan.addr6 == "2001:db8:100::1"
    assert plan.default_range4() == ("172.16.100.10", "172.16.100.99")
    assert plan.na_pool6() == "2001:db8:100::1000-2001:db8:100::1fff"


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("28=172.16.100.127", ("28", "172.16.100.127")),
        (" 42 = 1.2.3.4,5.6.7.8", ("42", "1.2.3.4,5.6.7.8")),
        ("ntp-server=1.2.3.4", ("option:ntp-server", "1.2.3.4")),
        ("option:domain-name=lab", ("option:domain-name", "lab")),
    ],
)
def test_parse_dhcp_option(spec, expected):
    assert render.parse_dhcp_option(spec) == expected


@pytest.mark.parametrize("spec", ["28", "=1", "28=", "0=1", "255=1"])
def test_parse_dhcp_option_bad(spec):
    with pytest.raises(ValueError):
        render.parse_dhcp_option(spec)


def test_dnsmasq_dhcp4():
    conf = render.dnsmasq_dhcp4(
        "dpw302",
        WanPlan(),
        "/run/dprobe/102/dnsmasq.leases",
        "/run/dprobe/102/dnsmasq.pid",
        options=[("28", "172.16.100.127")],
    )
    lines = conf.splitlines()
    assert "interface=dpw302" in lines
    assert "port=0" in lines
    assert "dhcp-range=172.16.100.10,172.16.100.99,255.255.255.0,12h" in lines
    assert "dhcp-option=option:router,172.16.100.1" in lines
    assert "dhcp-option-force=28,172.16.100.127" in lines


def test_dnsmasq_dhcp4_dns_and_range():
    conf = render.dnsmasq_dhcp4(
        "d",
        WanPlan(),
        "l",
        "p",
        range4=("172.16.100.50", "172.16.100.60"),
        dns=True,
        hosts=[("aftr.lab", "2001:db8:100::1")],
    )
    assert "dhcp-range=172.16.100.50,172.16.100.60,255.255.255.0,12h" in conf
    assert "port=53" in conf
    assert "host-record=aftr.lab,2001:db8:100::1" in conf
    with pytest.raises(ValueError):
        render.dnsmasq_dhcp4("d", WanPlan(), "l", "p", range4=("10.0.0.1", "10.0.0.9"))


def test_kea_dhcp6():
    conf = json.loads(
        render.kea_dhcp6(
            "dpw302", WanPlan(), "/var/lib/kea/x.csv", preferred=60, valid=120
        )
    )["Dhcp6"]
    assert conf["interfaces-config"]["interfaces"] == ["dpw302"]
    assert (conf["renew-timer"], conf["rebind-timer"]) == (30, 48)
    assert conf["preferred-lifetime"] == 60
    assert conf["valid-lifetime"] == 120
    subnet = conf["subnet6"][0]
    assert subnet["subnet"] == "2001:db8:100::/64"
    assert subnet["pools"] == [{"pool": "2001:db8:100::1000-2001:db8:100::1fff"}]
    assert subnet["pd-pools"] == [
        {"prefix": "2001:db8:200::", "prefix-len": 48, "delegated-len": 60}
    ]


def test_kea_dhcp6_only_pd():
    subnet = json.loads(render.kea_dhcp6("d", WanPlan(), "l", na=False))["Dhcp6"][
        "subnet6"
    ][0]
    assert "pools" not in subnet
    assert "pd-pools" in subnet


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pd_len": 48},
        {"pd_len": 65},
        {"preferred": 200, "valid": 100},
        {"t1": 50, "t2": 40},
    ],
)
def test_kea_dhcp6_bad(kwargs):
    with pytest.raises(ValueError):
        render.kea_dhcp6("d", WanPlan(), "l", **kwargs)


def test_radvd():
    conf = render.radvd("dpw302", WanPlan(), managed=False, router_lifetime=0)
    assert "interface dpw302 {" in conf
    assert "AdvManagedFlag off;" in conf
    assert "AdvOtherConfigFlag on;" in conf
    assert "AdvDefaultLifetime 0;" in conf
    assert "prefix 2001:db8:100::/64 {" in conf
    assert "RDNSS 2001:db8:100::1 {" in conf
    assert conf.count("{") == conf.count("}")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_interval": 2},
        {"min_interval": 14, "max_interval": 15},
        {"router_lifetime": 5},
    ],
)
def test_radvd_bad(kwargs):
    with pytest.raises(ValueError):
        render.radvd("d", WanPlan(), **kwargs)


def test_chrony():
    conf = render.chrony_server("172.16.100.1", "/run/chrony/dprobe-102.pid")
    assert "bindaddress 172.16.100.1\n" in conf
    assert "cmdport 0\n" in conf
    assert "local stratum 8\n" in conf
