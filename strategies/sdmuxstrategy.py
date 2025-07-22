import enum

import attr
from labgrid.driver import USBSDMuxDriver, USBStorageDriver
from labgrid.factory import target_factory
from labgrid.strategy.common import Strategy, StrategyError


class Status(enum.Enum):
    unknown = 0
    uboot = 1
    shell = 2


@target_factory.reg_driver
@attr.s(eq=False)
class SDMuxStrategy(Strategy):
    """UbootStrategy - Strategy to switch to uboot or shell"""

    bindings = {
        "power": "PowerProtocol",
        "console": "ConsoleProtocol",
        "shell": "ShellDriver",
        "sdmux": USBSDMuxDriver,
        "storage": USBStorageDriver,
    }

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
        elif status == Status.shell:
            self.target.activate(self.power)
            self.target.activate(self.storage)
            self.target.activate(self.sdmux)
            # power off
            self.power.off()
            # configure sd-mux
            self.sdmux.set_mode("host")

            self.storage.write_image(self.target.env.config.get_image_path("root"))

            self.sdmux.set_mode("dut")
            # cycle power
            self.power.on()

            self.target.activate(self.shell)
        else:
            raise StrategyError(f"no transition found from {self.status} to {status}")
        self.status = status
