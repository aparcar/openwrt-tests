"""labgrid driver for the Android Wi-Fi probe (contrib/android-probe).

A rooted Android phone on USB at a lab host, joined to the DUT's AP as a
real Wi-Fi client. The `aprobe` CLI on that host prints one JSON reply and
exits 0 (ok), 1 (ran, but the expected thing didn't happen) or 2 (error);
this driver runs it over SSH (labgrid's SSHDriver on the probe place's
NetworkService, so LG_PROXY and the exporter proxy apply as for any other
target).

    phone = target.get_driver("AndroidProbeDriver")
    phone.info()["wifi_mac"]
    phone.join("owci-1a2b3c-5g-0", "sae", key="openwrt4all")
    phone.log(20)
    phone.leave()
"""

import json
import shlex
from typing import ClassVar

import attr
from labgrid.driver.common import Driver
from labgrid.factory import target_factory
from labgrid.step import step

# aprobe exit codes (contrib/android-probe/aprobe.py)
OK = 0
FAILED = 1
ERROR = 2


class AndroidProbeError(Exception):
    """aprobe failed: exit 1 or 2, transport, or output that isn't JSON."""

    def __init__(self, message, reply=None):
        super().__init__(message)
        self.reply = reply or {}


def to_argv(*args, **options):
    """Positional args, then options as flags: foo_bar=1 -> --foo-bar 1;
    True -> --flag; False -> --no-flag; None is left out."""
    argv = [str(a) for a in args]
    for key, value in options.items():
        flag = key.replace("_", "-")
        if value is None:
            continue
        if value is True:
            argv.append(f"--{flag}")
        elif value is False:
            argv.append(f"--no-{flag}")
        else:
            argv += [f"--{flag}", str(value)]
    return argv


def parse_reply(rc, stdout, stderr=""):
    """aprobe's (exit code, stdout) -> reply dict; raise AndroidProbeError
    unless it exited 0 with a JSON object on its last line."""
    text = stdout.strip()
    try:
        reply = json.loads(text.splitlines()[-1]) if text else None
    except ValueError:
        reply = None
    if rc in (FAILED, ERROR) and isinstance(reply, dict):
        raise AndroidProbeError(f"aprobe: {reply.get('error', 'failed')}", reply)
    if rc != OK or not isinstance(reply, dict):
        detail = (stderr or text).strip()[-500:]
        raise AndroidProbeError(f"aprobe exited {rc}: {detail or 'no output'}")
    return reply


@target_factory.reg_driver
@attr.s(eq=False)
class AndroidProbeDriver(Driver):
    """Run `aprobe` on the Android probe's host and parse its JSON reply."""

    bindings: ClassVar[dict] = {"command": "CommandProtocol"}

    command_prefix = attr.ib(
        default="/usr/local/bin/aprobe",
        validator=attr.validators.instance_of(str),
    )
    timeout = attr.ib(default=90, validator=attr.validators.instance_of(int))

    @Driver.check_active
    @step(args=["args"])
    def check(self, *args, timeout=None, **options):
        """Run aprobe, return its reply; raise AndroidProbeError otherwise."""
        argv = shlex.split(self.command_prefix) + to_argv(*args, **options)
        stdout, stderr, rc = self.command.run(
            shlex.join(argv), timeout=timeout or self.timeout
        )
        return parse_reply(rc, "\n".join(stdout), "\n".join(stderr))

    def info(self):
        """{ serial, model, android, sdk, wifi_mac, wlan0_mac,
        battery_percent, charging, wifi_status }"""
        return self.check("info")

    def join(self, ssid, encryption="any", key=None, hidden=False):
        """Forget all networks and join `ssid`. Returns at once; the phone
        joins in the background. encryption: the DUT's uci encryption (none,
        psk2, psk-mixed, sae, sae-mixed) or "any" (WPA2, which Android
        upgrades to WPA3 on a transition AP, as for a user typing the
        password)."""
        return self.check(
            "join", ssid=ssid, encryption=encryption, key=key, hidden=hidden
        )

    def leave(self):
        """Forget every saved network: the phone leaves the DUT's AP."""
        return self.check("leave")

    def log(self, seconds=20):
        """The phone's Wi-Fi logcat lines, recorded for `seconds` from now."""
        return self.check("log", seconds=seconds, timeout=seconds + 60)["lines"]
