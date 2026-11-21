# SPDX-License-Identifier: LGPL-2.1-only
import json

import pytest
from dprobe import cli, slot
from dprobe.util import ERROR, FAILED, OK, ProbeError, mac_for, vlan_id
from drivers.debianprobedriver import DebianProbeError, parse_reply, to_argv


def parse(argv):
    return cli.build_parser().parse_args([str(a) for a in argv])


def test_parse_slot_up():
    args = parse(["slot", "up", 102, "--wan-vlan", 302])
    assert (args.func, args.lan_vlan, args.wan_vlan) == (cli.cmd_slot_up, 102, 302)


def test_parse_wan_dhcp4():
    args = parse(
        [
            "wan",
            "dhcp4",
            102,
            "--option",
            "28=172.16.100.127",
            "--option",
            "42=1.2.3.4",
            "--range",
            "172.16.100.50,172.16.100.60",
        ]
    )
    assert args.option == ["28=172.16.100.127", "42=1.2.3.4"]
    assert args.range == ("172.16.100.50", "172.16.100.60")


def test_parse_ra_capture_defaults():
    args = parse(["ra-capture", 102])
    assert (args.side, args.duration, args.count, args.solicit) == (
        "lan",
        10.0,
        None,
        True,
    )


@pytest.mark.parametrize(
    ("args", "options", "expected"),
    [
        (("slot", "up", 102), {"wan_vlan": 302}, {"wan_vlan": 302}),
        (("slot", "up", 102), {"wan_vlan": None}, {"wan_vlan": None}),
        (
            ("wan", "dhcp6", 102),
            {"na": False, "preferred": 60, "valid": 120, "t1": 20, "t2": 40},
            {"na": False, "pd": True, "preferred": 60, "t1": 20},
        ),
        (("wan", "dhcp4", 102), {"option": ["28=1.2.3.4"]}, {"option": ["28=1.2.3.4"]}),
        (
            ("ra-capture", 102),
            {"duration": 30, "solicit": False},
            {"duration": 30.0, "solicit": False},
        ),
        (
            ("dhcp4", 102),
            {"server": "192.168.1.1", "apply": True},
            {"apply": True, "request": True},
        ),
        (
            ("dns-pair", 102, "192.168.1.1", "x.lan"),
            {"tries": 20, "gap": 0.2},
            {"tries": 20, "gap": 0.2},
        ),
        (("wan", "stop", 102, "dhcp6"), {}, {"service": "dhcp6"}),
        (("logs", 102, "dhcp6"), {"lines": 40}, {"lines": 40}),
    ],
)
def test_driver_argv_matches_cli(args, options, expected):
    """What the driver builds is what the CLI parses."""
    parsed = parse(to_argv(*args, **options))
    for key, value in expected.items():
        assert getattr(parsed, key) == value


def test_main_bad_arguments(capsys):
    assert cli.main(["slot", "up", "notanumber"]) == ERROR
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {
        "error": "bad arguments"
    }


def test_main_slot_list(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(slot, "RUN_DIR", tmp_path)
    for vid in (110, 102):
        (tmp_path / str(vid)).mkdir()
        (tmp_path / str(vid) / "slot.json").write_text("{}")
    (tmp_path / "103").mkdir()  # half-built, no slot.json
    assert cli.main(["slot", "list"]) == OK
    assert json.loads(capsys.readouterr().out) == {"slots": [102, 110]}


def test_main_slot_not_up(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(slot, "RUN_DIR", tmp_path)
    assert cli.main(["wan", "leases", "102"]) == ERROR
    reply = json.loads(capsys.readouterr().out)
    assert reply == {"error": "slot 102 is not up", "not_up": True}


def test_parse_ping():
    out = (
        "3 packets transmitted, 2 received, 33.3333% packet loss, time 2003ms\n"
        "rtt min/avg/max/mdev = 0.412/0.538/0.664/0.126 ms\n"
    )
    assert cli.parse_ping(out) == {
        "transmitted": 3,
        "received": 2,
        "rtt_min": 0.412,
        "rtt_avg": 0.538,
        "rtt_max": 0.664,
    }
    assert cli.parse_ping("ping: connect: Network is unreachable") == {
        "transmitted": 0,
        "received": 0,
    }


def test_parse_reply():
    ok = parse_reply(0, '{"slots": []}')
    assert ok == {"slots": []} and ok.ok
    failed = parse_reply(1, 'noise\n{"error": "no lease", "offers": []}')
    assert not failed.ok and failed["error"] == "no lease"
    with pytest.raises(DebianProbeError, match="slot 102 is not up") as e:
        parse_reply(2, '{"error": "slot 102 is not up", "not_up": true}')
    assert e.value.reply["not_up"]
    with pytest.raises(DebianProbeError, match="exited 255"):
        parse_reply(255, "", "ssh: connect to host: Connection refused")
    with pytest.raises(DebianProbeError, match="exited 0"):
        parse_reply(0, "not json")


def test_vlan_and_mac():
    assert vlan_id("102") == 102
    for bad in (0, 4095, "x", None):
        with pytest.raises(ProbeError):
            vlan_id(bad)
    assert mac_for(0, 102) == "02:44:50:00:00:66"
    assert mac_for(1, 4094) == "02:44:50:01:0f:fe"


def test_vlan_policy():
    assert slot.parse_vlan_list("101,110-112") == {101, 110, 111, 112}
    assert slot.parse_vlan_list([101, "300-301"]) == {101, 300, 301}
    config = {"mgmt_vlan": 1, "allowed_vlans": ["100-199", "300-399"]}
    slot.check_vlan(config, 102)
    for vid in (1, 200):
        with pytest.raises(ProbeError):
            slot.check_vlan(config, vid)
    slot.check_vlan({}, 200)  # no policy: anything goes


def test_slot_describe():
    s = slot.Slot(lan_vlan=102, trunk="enp1s0", wan_vlan=302)
    d = s.describe()
    assert (d["lan_ns"], d["lan_dev"], d["wan_ns"], d["wan_dev"]) == (
        "dp-lan102",
        "dpl102",
        "dp-wan102",
        "dpw102",
    )
    assert d["wan_ip"] == "172.16.100.1"
    assert d["hostname"] == "dprobe102"
    assert s.side("wan") == ("dp-wan102", "dpw102")
    with pytest.raises(ProbeError):
        slot.Slot(lan_vlan=102, trunk="enp1s0").side("wan")


def test_exit_codes_match_driver():
    from drivers import debianprobedriver as drv

    assert (drv.OK, drv.FAILED, drv.ERROR) == (OK, FAILED, ERROR)
