"""aprobe's parsers, without a phone: pytest contrib/android-probe"""

import shlex

from aprobe import (
    connect_argv,
    parse_battery,
    parse_factory_mac,
    parse_network_ids,
    shell_command,
)

# A Pixel 5 (Android 14) after connect-network ... wpa2: one network, two lines
LIST_NETWORKS = """\
Network Id      SSID                         Security type
0            owci-probe-check                 wpa2-psk
0            owci-probe-check                 wpa3-sae^
12           my net                       open
"""

BATTERY = """\
Current Battery Service state:
  AC powered: false
  USB powered: true
  status: 2
  level: 81
"""


def test_network_ids():
    assert parse_network_ids(LIST_NETWORKS) == [0, 12]
    assert parse_network_ids("No networks\n") == []
    assert parse_network_ids("") == []


def test_factory_mac():
    dump = "x=1\nwifi_sta_factory_mac_address=58:24:29:8D:A4:0D\nmSave=true\n"
    assert parse_factory_mac(dump) == "58:24:29:8d:a4:0d"
    assert parse_factory_mac("Wi-Fi is enabled\n") is None


def test_battery():
    assert parse_battery(BATTERY) == (81, True)
    assert parse_battery("  level: 30\n  status: 3\n") == (30, False)
    assert parse_battery("") == (None, False)


def test_connect_argv():
    assert connect_argv("a b", "none", None, False) == [
        "cmd",
        "wifi",
        "connect-network",
        "a b",
        "open",
        "-r",
        "none",
    ]
    argv = connect_argv("x", "sae", "k'ey; reboot", True)
    assert argv[3:6] == ["x", "wpa3", "k'ey; reboot"]
    assert argv[-1] == "-h"


def test_shell_command_quotes_twice():
    """The phone's sh parses the string, then su's sh the inner one: the
    key stays one argument through both."""
    argv = connect_argv("my ssid", "psk2", "k'ey; reboot $x", False)
    outer = shlex.split(shell_command(argv))
    assert outer[:2] == ["su", "-c"]
    assert shlex.split(outer[2]) == argv
