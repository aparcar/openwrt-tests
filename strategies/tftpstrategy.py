import enum

import attr
from labgrid.driver import TFTPProviderDriver
from labgrid.factory import target_factory
from labgrid.strategy.common import Strategy, StrategyError


class Status(enum.Enum):
    unknown = 0
    off = 1
    uboot = 2
    shell = 3


@target_factory.reg_driver
@attr.s(eq=False)
class UBootTFTPStrategy(Strategy):
    """UbootStrategy - Strategy to switch to uboot or shell"""

    bindings = {
        "power": "PowerProtocol",
        "console": "ConsoleProtocol",
        "uboot": "LinuxBootProtocol",
        "shell": "ShellDriver",
        "tftp": "TFTPProviderDriver",
    }
    tftp: TFTPProviderDriver

    status = attr.ib(default=Status.unknown)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

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
            # cycle power
            p = self.tftp.stage(self.target.env.config.get_image_path("root"))
            self.power.cycle()
            # interrupt uboot
            self.target.activate(self.uboot)
            self.uboot.run_check(f"setenv bootfile {p}")
        elif status == Status.shell:
            # transition to uboot
            self.transition(Status.uboot)

            self.uboot.boot("")
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
            raise StrategyError("can not force state {}".format(status))
        self.status = status
