def test_echo(dut):
    dut.test_cmd("echo test", "test")
    dut.test_cmd("echo noch", "noch")


def test_ip(dut):
    dut.test_cmd("ip addr", "192.168.1.1/24")


def test_uci(dut):
    dut.test_cmd("uci set network.lan.proto=dhcp")
    dut.test_cmd("uci changes", "network.lan.proto='dhcp'")
