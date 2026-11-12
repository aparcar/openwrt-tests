"""labgrid driver for the iPhone Wi-Fi probe (contrib/iphone-probe).

An iPhone on USB at a host in the lab, joined to the DUT's AP as a real
Wi-Fi client. The `iprobe` CLI on that host prints one JSON reply and exits
0 (ok), 1 (ran, but the expected thing didn't happen) or 2 (error); this
driver runs it over SSH (labgrid's SSHDriver on the probe place's
NetworkService, so LG_PROXY and the exporter proxy apply as for any other
target).

    phone = target.get_driver("IPhoneProbeDriver")
    phone.info()["wifi_mac"]
    phone.join("owci-1a2b3c-5g-0", "sae-mixed", key="openwrt4all")
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

# iprobe exit codes (contrib/iphone-probe/iprobe.py)
OK = 0
FAILED = 1
ERROR = 2


class IPhoneProbeError(Exception):
    """iprobe failed: exit 1 or 2, transport, or output that isn't JSON."""

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
    """iprobe's (exit code, stdout) -> reply dict; raise IPhoneProbeError
    unless it exited 0 with a JSON object on its last line."""
    text = stdout.strip()
    try:
        reply = json.loads(text.splitlines()[-1]) if text else None
    except ValueError:
        reply = None
    if rc in (FAILED, ERROR) and isinstance(reply, dict):
        raise IPhoneProbeError(f"iprobe: {reply.get('error', 'failed')}", reply)
    if rc != OK or not isinstance(reply, dict):
        detail = (stderr or text).strip()[-500:]
        raise IPhoneProbeError(f"iprobe exited {rc}: {detail or 'no output'}")
    return reply


@target_factory.reg_driver
@attr.s(eq=False)
class IPhoneProbeDriver(Driver):
    """Run `iprobe` on the iPhone probe's host and parse its JSON reply."""

    bindings: ClassVar[dict] = {"command": "CommandProtocol"}

    command_prefix = attr.ib(
        default="/usr/local/bin/iprobe",
        validator=attr.validators.instance_of(str),
    )
    timeout = attr.ib(default=90, validator=attr.validators.instance_of(int))

    @Driver.check_active
    @step(args=["args"])
    def check(self, *args, timeout=None, **options):
        """Run iprobe, return its reply; raise IPhoneProbeError otherwise."""
        argv = shlex.split(self.command_prefix) + to_argv(*args, **options)
        stdout, stderr, rc = self.command.run(
            shlex.join(argv), timeout=timeout or self.timeout
        )
        return parse_reply(rc, "\n".join(stdout), "\n".join(stderr))

    def info(self):
        """{ udid, name, hardware_model, ios, build, wifi_mac, supervised,
        battery_percent, charging, joined_profile }"""
        return self.check("info")

    def join(self, ssid, encryption="any", key=None, hidden=False):
        """Install the Wi-Fi profile for `ssid`, replacing the previous one.
        Returns at once; the phone joins in the background. encryption: the
        DUT's uci encryption (none, psk2, psk-mixed, sae, sae-mixed) or
        "any" (the phone picks, as when a user types the password)."""
        return self.check(
            "join", ssid=ssid, encryption=encryption, key=key, hidden=hidden
        )

    def leave(self):
        """Remove the profile: the phone forgets the network and leaves it."""
        return self.check("leave")

    def log(self, seconds=20):
        """The phone's wifid log lines about joining, recorded for `seconds`
        from now (e.g. "no scan candidate network": it sees no network that
        matches its profile, so it never tries)."""
        return self.check("log", seconds=seconds, timeout=seconds + 60)["lines"]
