import enum
import ipaddress
import os
from typing import ClassVar
from urllib.parse import urlparse

import attr
import pexpect
from labgrid.driver import TFTPProviderDriver
from labgrid.factory import target_factory
from labgrid.strategy.common import Strategy, StrategyError
from labgrid.util import Timeout
from labgrid.util.managedfile import ManagedFile

HASH_LEN = 16

# Broadcom CFE ends every command with this line
STATUS_RE = r"\*\*\* command status = (-?\d+)"
# CFE crashes print this, then reset the board
EXCEPTION_RE = r"\*\*Exception \d+: [^\r\n]*"
# ASUS CFE prints no command status after a successful `flash`
FLASHED_RE = r"done\. \d+ bytes written"


class CFECommandError(StrategyError):
    """A CFE command returned a non-zero status (CFE itself kept running)."""


class Status(enum.Enum):
    unknown = 0
    off = 1
    cfe = 2
    shell = 3


@target_factory.reg_driver
@attr.s(eq=False)
class CFETFTPStrategy(Strategy):
    """Strategy for Broadcom CFE boards (bcm47xx) without a netbootable image.

    OpenWrt publishes no initramfs for bcm47xx, only the flashable TRX, so
    every boot writes the image to flash: interrupt CFE with Ctrl+C,
    `flash -noheader <server>:<file> <flash_device>` over TFTP, power cycle,
    and let CFE boot the new firmware from flash.

    With `configure_ip` (default), CFE gets the address after the TFTP
    server's (`ifconfig`); without it CFE keeps its own address (192.168.1.1
    on ASUS), so the TFTP server must be in that subnet. A `ping` of the
    server checks that CFE can receive before the transfer starts.
    """

    bindings: ClassVar[dict] = {
        "power": "PowerProtocol",
        "console": "ConsoleProtocol",
        "shell": "ShellDriver",
        "tftp": "TFTPProviderDriver",
    }
    tftp: TFTPProviderDriver

    prompt = attr.ib(default="CFE>", validator=attr.validators.instance_of(str))
    interface = attr.ib(default="eth0", validator=attr.validators.instance_of(str))
    configure_ip = attr.ib(default=True, validator=attr.validators.instance_of(bool))
    flash_device = attr.ib(
        default="flash1.trx", validator=attr.validators.instance_of(str)
    )
    interrupt_timeout = attr.ib(
        default=30.0, validator=attr.validators.instance_of((int, float))
    )
    flash_timeout = attr.ib(
        default=600.0, validator=attr.validators.instance_of((int, float))
    )
    # the first TFTP download after boot can give up ("break!! by max
    # retry!!") before ARP has settled; a failed download writes nothing
    flash_attempts = attr.ib(default=3, validator=attr.validators.instance_of(int))

    status = attr.ib(default=Status.unknown)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()
        self._image = None

    def _stage(self, filename):
        """Stage the image in the TFTP root under <sha256 prefix><ext>."""
        provider = self.tftp.provider
        mf = ManagedFile(filename, provider)
        name = mf.get_hash()[:HASH_LEN] + os.path.splitext(filename)[1]
        symlink = os.path.join(provider.internal, name)
        mf.sync_to_resource(symlink=symlink)
        return provider.external + symlink[len(provider.internal) :]

    def _interrupt(self):
        """Send Ctrl+C until CFE shows its prompt, then drain the console."""
        timeout = Timeout(self.interrupt_timeout)
        while not timeout.expired:
            self.console.write(b"\x03")
            try:
                self.console.expect(self.prompt, timeout=0.2)
                break
            except pexpect.TIMEOUT:
                continue
        else:
            raise StrategyError(f"no CFE prompt within {self.interrupt_timeout}s")
        # one prompt per extra Ctrl+C that was already on its way
        self.console.settle(quiet_time=1.0, timeout=10.0)
        self.console.sendline("")
        self.console.expect(self.prompt, timeout=5)

    def _run(self, cmd, timeout=30.0, done=None):
        """Run a CFE command; `done` is a pattern that also means success."""
        self.console.sendline(cmd)
        patterns = [STATUS_RE, EXCEPTION_RE] + ([done] if done else [])
        index, before, match, _ = self.console.expect(patterns, timeout=timeout)
        if index == 1:
            raise StrategyError(f"CFE crashed running {cmd!r}: {match.group(0)}")
        if index == 2:
            return
        self.console.expect(self.prompt, timeout=5)
        rc = int(match.group(1))
        if rc != 0:
            output = before.decode(errors="replace").strip()
            raise CFECommandError(f"CFE command {cmd!r} failed ({rc}): {output}")

    def _run_retry(self, cmd, attempts, timeout=30.0, done=None):
        """Run a command until CFE returns 0 (crashes are not retried)."""
        for attempt in range(1, attempts + 1):
            try:
                self._run(cmd, timeout=timeout, done=done)
                return
            except CFECommandError:
                if attempt == attempts:
                    raise
                self.logger.warning(
                    "%r attempt %d/%d failed, retrying", cmd, attempt, attempts
                )

    def transition(self, status):
        if not isinstance(status, Status):
            status = Status[status]
        if status == Status.unknown:
            raise StrategyError(f"can not transition to {status}")
        elif status == self.status:
            return  # nothing to do
        elif status == Status.off:
            self.target.deactivate(self.console)
            self.target.activate(self.power)
            self.power.off()
        elif status == Status.cfe:
            self.transition(Status.off)
            self.target.activate(self.tftp)
            self.target.activate(self.console)

            staged = urlparse(
                self._stage(self.target.env.config.get_image_path("root"))
            )
            if staged.scheme != "tftp":
                raise StrategyError(
                    "TFTPProvider external must be a tftp://<server>/<path>/ URL"
                )
            server = staged.hostname
            self._image = f"{server}:{staged.path.lstrip('/')}"

            self.power.cycle()
            self._interrupt()

            if self.configure_ip:
                dut = ipaddress.ip_address(server) + 1
                self._run(f"ifconfig {self.interface} -addr={dut} -mask=255.255.255.0")
            # fails fast (instead of a hanging TFTP transfer) when CFE can't
            # receive; CFE pings once, and the first one after boot often
            # gets no answer
            self._run_retry(f"ping {server}", attempts=10)
        elif status == Status.shell:
            self.transition(Status.cfe)

            self._run_retry(
                f"flash -noheader {self._image} {self.flash_device}",
                attempts=self.flash_attempts,
                timeout=self.flash_timeout,
                done=FLASHED_RE,
            )
            self.power.cycle()
            self.target.activate(self.shell)
        else:
            raise StrategyError(f"no transition found from {self.status} to {status}")
        self.status = status

    def force(self, status):
        if not isinstance(status, Status):
            status = Status[status]
        if status == Status.off:
            self.target.activate(self.power)
        elif status == Status.cfe:
            self.target.activate(self.console)
        elif status == Status.shell:
            self.target.activate(self.shell)
        else:
            raise StrategyError(f"can not force state {status}")
        self.status = status
