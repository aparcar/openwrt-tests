import enum
import ipaddress
import os
from typing import ClassVar
from urllib.parse import urlparse

import attr
from labgrid.driver import TFTPProviderDriver
from labgrid.factory import target_factory
from labgrid.strategy.common import Strategy, StrategyError
from labgrid.util.managedfile import ManagedFile

# U-Boot's TFTP client truncates the requested file name to 127 characters
# (MAX_LEN in net/tftp.c), which full OpenWrt image names easily exceed
HASH_LEN = 16


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

    # `version` output of the bootloader, read at its prompt before booting;
    # None if it has no such command (test_base.py records it)
    uboot_version = attr.ib(default=None)

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

            self.power.cycle()
            for expect, send in self.console_unlock:
                self.console.expect(expect, timeout=60)
                self.console.write(send.encode())
            # interrupt uboot

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

            self.target.activate(self.uboot)
            # bookkeeping only: vendor bootloaders may lack `version`
            # (Aruba APBoot answers "APBoot 1.5.7.2 (build ...)")
            out, _, _ = self.uboot.run("version", timeout=30)
            banner = [
                line.strip()
                for line in out
                if line.strip() and "nknown command" not in line
            ]
            if banner:
                self.uboot_version = banner[0]
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
