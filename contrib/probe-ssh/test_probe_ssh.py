"""probe-ssh's filter: pytest contrib/probe-ssh"""

import shlex

from probe_ssh import allowed

IPROBE = "/usr/local/bin/iprobe"


def test_allows_the_probe_and_keepalive():
    # What the drivers send: shlex.join of the argv, as one string
    sent = shlex.join([IPROBE, "join", "--ssid", "a b; reboot", "--key", "k'$x"])
    assert allowed("iprobe", sent) == [
        IPROBE,
        "join",
        "--ssid",
        "a b; reboot",
        "--key",
        "k'$x",
    ]
    assert allowed("iprobe", "iprobe info") == [IPROBE, "info"]
    # labgrid SSHDriver's keepalive
    assert allowed("iprobe", "cat") == ["cat"]


def test_refuses_everything_else():
    for original in (
        None,  # interactive login
        "",
        "sh",
        "cat /etc/iprobe/keybag.pem",
        "aprobe info",  # another probe's command
        "/tmp/iprobe info",
        "iprobe 'unbalanced",
    ):
        assert allowed("iprobe", original) is None, original
    # No shell: ";" stays an argument, which the probe's argparse rejects
    assert allowed("iprobe", "iprobe info; sh") == [IPROBE, "info;", "sh"]
