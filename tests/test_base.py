def test_echo(dut):
    dut.send_cmd("echo test", "test")
    dut.send_cmd("echo noch", "noch")


def test_ip(dut):
    dut.send_cmd("ip addr", "192.168.1.1/24")


def test_uci(dut):
    dut.send_cmd("uci set network.lan.proto=dhcp")
    dut.send_cmd("uci changes", "network.lan.proto='dhcp'")


def test_uname(dut):
    dut.send_cmd("uname -a", "GNU/Linux")
