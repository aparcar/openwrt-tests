"""EFI targets must really boot through UEFI.

OpenWrt's x86 combined-efi images also carry a BIOS boot path, so a target
whose UEFI firmware never reaches QEMU still boots (through SeaBIOS) and
every other test passes. This test catches that.
"""

import pytest


def test_booted_via_uefi(env, ssh_command):
    """On targets with `openwrt.efi_firmware`, the kernel was started by UEFI."""
    if not env.config.data.get("openwrt", {}).get("efi_firmware"):
        pytest.skip("not an EFI target")
    _, _, rc = ssh_command.run("test -d /sys/firmware/efi")
    assert rc == 0, "no /sys/firmware/efi: the kernel was not booted via UEFI"
