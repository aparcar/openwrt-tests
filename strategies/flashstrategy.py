import enum
import hashlib
import os
import shlex
from typing import ClassVar

import attr
from labgrid.factory import target_factory
from labgrid.strategy.common import Strategy, StrategyError


class Status(enum.Enum):
    unknown = 0
    off = 1
    shell = 2


@target_factory.reg_driver
@attr.s(eq=False)
class FlashBootStrategy(Strategy):
    """Strategy for boards whose bootloader can't fetch an image, but boots
    the uImage at the start of a flash partition (Ubiquiti USW Aggregation:
    its U-Boot TFTP over an SFP+ module fails, OpenWrt's SFP support works).

    The board boots whatever is in flash; the image under test goes there
    from that system: copy it over SSH, `mtd write` it to `partition` and
    power cycle. An initramfs image still runs from RAM, and the next run
    writes its own image from it. A flash that already holds the image
    isn't written again (a second boot in the same session).

    If the image in flash doesn't reach a shell, nothing can replace it
    from here: recover the board through its bootloader (see the lab's
    README).
    """

    bindings: ClassVar[dict] = {
        "power": "PowerProtocol",
        "console": "ConsoleProtocol",
        "shell": "ShellDriver",
        "ssh": "SSHDriver",
    }

    partition = attr.ib(default="firmware", validator=attr.validators.instance_of(str))
    flash_timeout = attr.ib(
        default=600, validator=attr.validators.instance_of((int, float))
    )

    status = attr.ib(default=Status.unknown)

    def _boot(self):
        """Power cycle and wait for the shell of what is in flash."""
        self.target.deactivate(self.ssh)
        self.target.deactivate(self.shell)
        self.power.cycle()
        self.target.activate(self.shell)
        # as in tftpstrategy: kernel lines must not break command output
        self.shell.run_check("dmesg -n 1")

    def _flash(self, image):
        """Write `image` to the partition unless it is there already."""
        size = os.path.getsize(image)
        with open(image, "rb") as f:
            md5 = hashlib.md5(f.read()).hexdigest()
        mtd = self.shell.run_check(
            f"grep '\"{self.partition}\"' /proc/mtd | cut -d: -f1"
        )
        if not mtd:
            raise StrategyError(f"no mtd partition {self.partition!r}")
        dev = f"/dev/{mtd[0]}"
        in_flash = self.shell.run_check(f"head -c {size} {dev} | md5sum")
        if in_flash and in_flash[0].split()[0] == md5:
            return False

        remote = "/tmp/" + os.path.basename(image)
        self.target.activate(self.ssh)
        self.ssh.put(image, remote)
        self.target.deactivate(self.ssh)
        self.shell.run_check(
            f"mtd write {shlex.quote(remote)} {self.partition}",
            timeout=self.flash_timeout,
        )
        self.shell.run_check(f"mtd verify {shlex.quote(remote)} {self.partition}")
        return True

    def transition(self, status):
        if not isinstance(status, Status):
            status = Status[status]
        if status == Status.unknown:
            raise StrategyError(f"can not transition to {status}")
        elif status == self.status:
            return  # nothing to do
        elif status == Status.off:
            self.target.deactivate(self.ssh)
            self.target.deactivate(self.shell)
            self.target.activate(self.power)
            self.power.off()
        elif status == Status.shell:
            self.target.activate(self.power)
            self.target.activate(self.console)
            self._boot()
            if self._flash(self.target.env.config.get_image_path("root")):
                self._boot()
        else:
            raise StrategyError(f"no transition found from {self.status} to {status}")
        self.status = status

    def force(self, status):
        if not isinstance(status, Status):
            status = Status[status]
        if status == Status.off:
            self.target.activate(self.power)
        elif status == Status.shell:
            self.target.activate(self.shell)
        else:
            raise StrategyError(f"can not force state {status}")
        self.status = status
