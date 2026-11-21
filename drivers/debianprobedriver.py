"""labgrid driver for the Debian lab probe (contrib/debian-probe).

The probe is a Debian box on the lab trunk. Its `dprobe` CLI runs as root
through a sudoers rule limited to that one command, prints one JSON document
and exits 0 (ok), 1 (ran, but the expected thing didn't happen) or 2 (error).
This driver runs it over SSH (any CommandProtocol, normally labgrid's
SSHDriver bound to the probe's NetworkService, so LG_PROXY and the exporter
proxy apply as for any other target).

    probe = target.get_driver("DebianProbeDriver")
    probe.call("slot", "up", 102, wan_vlan=302)
    r = probe.call("ra-capture", 102, duration=15)   # r.ok is False on exit 1
"""

import json
import shlex
from typing import ClassVar

import attr
from labgrid.driver.common import Driver
from labgrid.factory import target_factory
from labgrid.step import step

# dprobe exit codes (contrib/debian-probe/dprobe/util.py)
OK = 0
FAILED = 1
ERROR = 2


class DebianProbeError(Exception):
    """dprobe could not run the command (exit 2, transport, bad output)."""

    def __init__(self, message, reply=None):
        super().__init__(message)
        self.reply = reply or {}


class Result(dict):
    """A dprobe reply (dict) plus `ok`: False if dprobe exited with 1."""

    def __init__(self, reply, ok):
        super().__init__(reply)
        self.ok = ok

    def __repr__(self):
        return f"Result(ok={self.ok}, {dict.__repr__(self)})"


def to_argv(*args, **options):
    """Positional args, then options as flags.

    foo_bar=1 -> --foo-bar 1; True -> --flag; False -> --no-flag (dprobe's
    BooleanOptionalAction) ; None is left out; a list repeats the option.
    """
    argv = [str(a) for a in args]
    for key, value in options.items():
        flag = "--" + key.replace("_", "-")
        if value is None:
            continue
        if value is True:
            argv.append(flag)
        elif value is False:
            argv.append("--no-" + key.replace("_", "-"))
        elif isinstance(value, (list, tuple)):
            for v in value:
                argv += [flag, str(v)]
        else:
            argv += [flag, str(value)]
    return argv


def parse_reply(rc, stdout, stderr=""):
    """dprobe's (exit code, stdout) -> Result; raise DebianProbeError on
    exit 2, unknown codes (e.g. 255 from ssh) or output that isn't JSON."""
    text = stdout.strip()
    try:
        reply = json.loads(text.splitlines()[-1]) if text else None
    except ValueError:
        reply = None
    if rc == ERROR and isinstance(reply, dict):
        raise DebianProbeError(f"dprobe: {reply.get('error', 'error')}", reply)
    if rc not in (OK, FAILED) or not isinstance(reply, dict):
        detail = (stderr or text).strip()[-500:]
        raise DebianProbeError(f"dprobe exited {rc}: {detail or 'no output'}")
    return Result(reply, ok=rc == OK)


@target_factory.reg_driver
@attr.s(eq=False)
class DebianProbeDriver(Driver):
    """Run `dprobe` on the Debian probe and parse its JSON reply."""

    bindings: ClassVar[dict] = {"command": "CommandProtocol"}

    command_prefix = attr.ib(
        default="sudo -n /usr/local/sbin/dprobe",
        validator=attr.validators.instance_of(str),
    )
    timeout = attr.ib(default=120, validator=attr.validators.instance_of(int))

    @Driver.check_active
    def run(self, *args, timeout=None, **options):
        """Run dprobe, return a Result (`.ok` False on exit 1).

        Raises DebianProbeError on exit 2 or a transport failure."""
        argv = shlex.split(self.command_prefix) + to_argv(*args, **options)
        stdout, stderr, rc = self.command.run(
            shlex.join(argv), timeout=timeout or self.timeout
        )
        return parse_reply(rc, "\n".join(stdout), "\n".join(stderr))

    @Driver.check_active
    @step(args=["args"])
    def call(self, *args, timeout=None, **options):
        """Like run(); kept separate so the labgrid step log shows calls."""
        return self.run(*args, timeout=timeout, **options)

    @Driver.check_active
    def check(self, *args, timeout=None, **options):
        """Like call(), but exit 1 raises DebianProbeError as well."""
        result = self.call(*args, timeout=timeout, **options)
        if not result.ok:
            raise DebianProbeError(
                f"dprobe {' '.join(map(str, args))}: {result.get('error', 'failed')}",
                result,
            )
        return result
