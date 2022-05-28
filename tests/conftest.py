import pytest

import pexpect
import sys


def pytest_addoption(parser):
    parser.addoption("--target", action="store", default="x86/64")


class Qemu:
    def __init__(self, pytestconfig):
        self.child = None
        self.pytestconfig = pytestconfig
        self.luci_port = None
        self.ssh_port = None

    def start(self):
        target, subtarget = self.pytestconfig.getoption("target").split("/")
        qemu_cmd = (
            f"./scripts/qemustart {target} {subtarget} "
            "--netdev user,id=lan,net=192.168.1.0/24,dhcpstart=192.168.1.100,restrict=yes,hostfwd=tcp::8022-:22 "
            "--netdev user,id=wan "
            "-net nic,netdev=lan -net nic,netdev=wan -device virtio-rng-pci "
        )
        print(f"start qemu: \n{qemu_cmd}")

        self.child = pexpect.spawn(qemu_cmd, logfile=sys.stdout.buffer)
        self.child.expect("Please press Enter to activate this console.", timeout=80)
        self.child.expect("link becomes ready", timeout=90)
        self.child.send("\n")
        self.child.expect(":/#", timeout=10)

    def stop(self):
        self.child.close(force=True)
        self.child = None

    def send_cmd(self, cmd, expect=None):
        self.child.sendline(cmd)
        if expect:
            self.child.expect(expect)
        self.child.expect(":/#")

    def expect(self, expect):
        self.child.expect(expect)


@pytest.fixture
def dut(pytestconfig):
    dut = Qemu(pytestconfig)
    dut.start()
    yield dut
    dut.stop()
