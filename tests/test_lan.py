from ipaddress import IPv4Interface
from time import sleep

from conftest import ubus_call


def test_lan_wait_for_link_ready(shell_command):
    for _ in range(60):
        if shell_command.run("dmesg | grep br-lan | grep forwarding")[2] == 0:
            return
        sleep(1)

    assert False, "LAN interface did not come up within 60 seconds"


def test_lan_wait_for_network(shell_command):
    for _ in range(60):
        if ubus_call(shell_command, "network.interface.lan", "status").get(
            "ipv4-address"
        ):
            return

    assert False, "LAN interface did not come up within 60 seconds"


def test_lan_interface_address(shell_command):
    assert shell_command.get_ip_addresses("br-lan")[0] == IPv4Interface(
        "192.168.1.1/24"
    )


def test_lan_interface_has_neighbor(shell_command):
    assert "3 packets transmitted, 3 packets received" in "\n".join(
        shell_command.run("ping -c 3 ff02::1%br-lan")[0]
    )
