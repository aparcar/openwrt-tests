from ipaddress import IPv4Interface


def test_lan_interface_address(shell_command):
    assert shell_command.get_ip_addresses("br-lan")[0] == IPv4Interface("192.168.1.1/24")
