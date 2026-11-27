"""The DUT's LAN bridge with its default configuration, seen from the DUT
itself: link, address and a neighbour on the wire.

The bridge is br-lan on most devices; Realtek switches name it `switch`
(board.json) and put LAN on its VLAN 1 (`switch.1`)."""

from ipaddress import IPv4Interface
from time import monotonic, sleep

from conftest import ubus_call


def lan_device(shell_command):
    """The LAN interface's Linux device (br-lan, switch.1, ...)."""
    return ubus_call(shell_command, "network.interface.lan", "status")["l3_device"]


def test_lan_wait_for_link_ready(shell_command):
    """The LAN bridge comes up and starts forwarding within 60 seconds of
    boot. If it doesn't, nothing on the LAN ports can reach the device."""
    bridge = (
        shell_command.run("jsonfilter -i /etc/board.json -e @.bridge.name")[0]
        or ["br-lan"]
    )[0]
    deadline = monotonic() + 60

    while monotonic() < deadline:
        if (
            shell_command.run(f"dmesg | grep '{bridge}: port' | grep forwarding")[2]
            == 0
        ):
            return

        sleep(1)

    assert False, "LAN interface did not come up within 60 seconds"


def test_lan_wait_for_network(shell_command):
    """netifd brings the LAN interface up with an IPv4 address within 60
    seconds. Without it the web interface and SSH are unreachable from the
    LAN."""
    deadline = monotonic() + 60

    while monotonic() < deadline:
        if ubus_call(shell_command, "network.interface.lan", "status").get(
            "ipv4-address"
        ):
            return

        sleep(1)

    assert False, "LAN interface did not come up within 60 seconds"


def test_lan_interface_address(shell_command, env):
    """The LAN bridge has OpenWrt's default address, 192.168.1.1/24. Users
    rely on it to reach a freshly flashed device.

    A target can override the expected address with `openwrt.lan_ipv4` in
    its YAML, for example when it uses an isolated test subnet.
    """
    expected_address = env.config.data.get("openwrt", {}).get(
        "lan_ipv4", "192.168.1.1/24"
    )

    addresses = shell_command.get_ip_addresses(lan_device(shell_command))
    assert addresses[0] == IPv4Interface(expected_address)


def test_lan_interface_has_neighbor(shell_command):
    """All three IPv6 all-nodes pings (ff02::1) on the LAN bridge get an
    answer, so IPv6 link-local traffic works on the LAN.

    The DUT answers this ping itself too, so it doesn't prove that another
    host is attached.
    """
    assert "3 packets transmitted, 3 packets received" in "\n".join(
        shell_command.run(f"ping -c 3 ff02::1%{lan_device(shell_command)}")[0]
    )
