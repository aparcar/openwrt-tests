from ipaddress import IPv4Interface
from time import monotonic, sleep

from conftest import ubus_call


def test_lan_wait_for_link_ready(shell_command):
    deadline = monotonic() + 60

    while monotonic() < deadline:
        if shell_command.run("dmesg | grep br-lan | grep forwarding")[2] == 0:
            return

        sleep(1)

    assert False, "LAN interface did not come up within 60 seconds"


def test_lan_wait_for_network(shell_command):
    # Give the LAN interface a reasonable amount of time to obtain an IPv4 address.
    deadline = monotonic() + 60

    while monotonic() < deadline:
        if ubus_call(shell_command, "network.interface.lan", "status").get(
            "ipv4-address"
        ):
            return

        sleep(1)

    assert False, "LAN interface did not come up within 60 seconds"


def test_lan_interface_address(shell_command, env):
    # Keep the standard OpenWrt LAN address unless the target explicitly
    # overrides it, for example when using an isolated test subnet.
    expected_address = env.config.data.get("openwrt", {}).get(
        "lan_ipv4", "192.168.1.1/24"
    )

    assert shell_command.get_ip_addresses("br-lan")[0] == IPv4Interface(
        expected_address
    )


def test_lan_interface_has_neighbor(shell_command):
    assert "3 packets transmitted, 3 packets received" in "\n".join(
        shell_command.run("ping -c 3 ff02::1%br-lan")[0]
    )
