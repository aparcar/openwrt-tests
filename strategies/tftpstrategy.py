import enum
import ipaddress
import os
import re
import time
from typing import ClassVar
from urllib.parse import urlparse

import attr
import pexpect
from labgrid.driver import TFTPProviderDriver
from labgrid.factory import target_factory
from labgrid.strategy.common import Strategy, StrategyError
from labgrid.util.managedfile import ManagedFile

# U-Boot's TFTP client truncates the requested file name to 127 characters
# (MAX_LEN in net/tftp.c), which full OpenWrt image names easily exceed
HASH_LEN = 16


def drain_console(console):
    """Drop what the console read before this power-on.

    Its expect buffer outlives the "off" state: after a test ended the system
    (panic, reboot), it still holds that boot's "stop autoboot" line, which
    the bootloader driver would match at once, interrupting before power-on
    and then missing the real countdown. Bounded in time: some unpowered
    boards' consoles emit noise without end (the AP-32x a stream of "|")."""
    deadline = time.monotonic() + 1
    try:
        while time.monotonic() < deadline:
            console.expect(r"(?s).+", timeout=0.2)
    except pexpect.TIMEOUT:
        pass


class Status(enum.Enum):
    unknown = 0
    off = 1
    uboot = 2
    shell = 3


@target_factory.reg_driver
@attr.s(eq=False)
class UBootTFTPStrategy(Strategy):
    """UbootStrategy - Strategy to switch to uboot or shell"""

    bindings: ClassVar[dict] = {
        "power": "PowerProtocol",
        "console": "ConsoleProtocol",
        "uboot": "LinuxBootProtocol",
        "shell": "ShellDriver",
        "tftp": "TFTPProviderDriver",
    }
    tftp: TFTPProviderDriver

    # For bootloaders at another speed than Linux (XikeStor: 9600, Linux
    # 115200): the exporter's speed is Linux's, the console runs at this one
    # until boot (RFC 2217 lets ser2net change the tty's speed)
    uboot_baudrate = attr.ib(default=0)

    # [expect regex, text to send] pairs worked through after power-on, before
    # the UBootDriver looks for its prompt: for vendor CLIs in front of U-Boot
    # (XikeStor: Ctrl+B, menu, Ctrl+F, password)
    console_unlock = attr.ib(default=attr.Factory(list))

    status = attr.ib(default=Status.unknown)

    # everything the bootloader printed from power-on up to its prompt
    # (banner, SoC, DRAM, ...); test_base.py records and parses it
    bootloader_log = attr.ib(default=None)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    def _stage(self, filename):
        """Like TFTPProviderDriver.stage(), but the symlink in the TFTP root
        is named <sha256 prefix><ext> to keep the bootfile path short."""
        provider = self.tftp.provider
        mf = ManagedFile(filename, provider)
        name = mf.get_hash()[:HASH_LEN] + os.path.splitext(filename)[1]
        symlink = os.path.join(provider.internal, name)
        mf.sync_to_resource(symlink=symlink)
        return provider.external + symlink[len(provider.internal) :]

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
        elif status == Status.uboot:
            self.transition(Status.off)
            self.target.activate(self.tftp)
            self.target.activate(self.console)
            drain_console(self.console)
            if self.uboot_baudrate:
                self.console.serial.baudrate = self.uboot_baudrate

            staged = self._stage(self.target.env.config.get_image_path("root"))
            parsed = urlparse(staged)

            if parsed.scheme == "tftp":
                # The exporter's TFTPProvider `external` is a full
                # tftp://<host>/<path> URL: the host becomes serverip (and the
                # DUT's ipaddr is derived from it), the path becomes bootfile.
                staged_file = parsed.path.lstrip("/")
                tftp_server_ip = parsed.hostname
            else:
                # Plain relative `external` path: no serverip is configured
                # (the board's U-Boot env or DHCP is expected to provide it).
                staged_file = staged
                tftp_server_ip = None

            self.uboot.init_commands = (
                f"setenv bootfile {staged_file}",
            ) + self.uboot.init_commands

            if tftp_server_ip:
                tftp_dut_ip = ipaddress.ip_address(tftp_server_ip) + 1
                # Aruba APBoot's tftpboot falls back to DHCP unless ipaddr,
                # netmask and gatewayip are all set; harmless for U-Boot.
                self.uboot.init_commands = (
                    f"setenv serverip {tftp_server_ip}",
                    f"setenv ipaddr {tftp_dut_ip}",
                    "setenv netmask 255.255.255.0",
                    f"setenv gatewayip {tftp_server_ip}",
                ) + self.uboot.init_commands

            # Record what is read from the console from power-on until the
            # bootloader prompt (bootloader_log). Only matches are kept: on a
            # timeout pexpect leaves the data in its buffer for the next match.
            seen = []
            console_expect = self.console.expect

            def recording_expect(pattern, timeout=-1):
                result = console_expect(pattern, timeout=timeout)
                if isinstance(result[3], bytes):
                    seen.append(result[1] + result[3])
                return result

            self.console.expect = recording_expect
            try:
                self.power.cycle()
                for expect, send in self.console_unlock:
                    self.console.expect(expect, timeout=60)
                    self.console.write(send.encode())
                # interrupt uboot
                self.target.activate(self.uboot)
            finally:
                del self.console.expect
            log = b"".join(seen).decode(errors="replace")
            prompt = re.search(self.uboot.prompt, log)
            self.bootloader_log = log[: prompt.start()] if prompt else log
        elif status == Status.shell:
            # transition to uboot
            self.transition(Status.uboot)

            self.uboot.boot("")
            if self.uboot_baudrate:
                # switch once the bootloader took the whole command (sent at
                # its speed), before the kernel prints its version
                self.console.expect("Booting kernel")
                self.console.serial.baudrate = self.console.port.speed
            self.uboot.await_boot()
            self.target.activate(self.shell)
        else:
            raise StrategyError(f"no transition found from {self.status} to {status}")
        self.status = status

    def force(self, status):
        if not isinstance(status, Status):
            status = Status[status]
        if status == Status.off:
            self.target.activate(self.power)
        elif status == Status.uboot:
            self.target.activate(self.uboot)
        elif status == Status.shell:
            self.target.activate(self.shell)
        else:
            raise StrategyError(f"can not force state {status}")
        self.status = status
