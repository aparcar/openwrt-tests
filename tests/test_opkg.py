import time

def test_opkg(dut):
    # Wait till network is really available
    # Todo try to find a better way to detect that the WAN is working.
    time.sleep(5)

    dut.send_cmd("opkg update")
    dut.expect("https://downloads.openwrt.org/")
    dut.expect("Signature check passed.")

    dut.send_cmd("opkg list-installed")
    dut.expect("procd ")

    dut.send_cmd("opkg install iperf3")
    dut.expect("Installing iperf3")
    dut.expect("Configuring iperf3")

    dut.send_cmd("opkg list-installed")
    dut.expect("iperf3 ")

    dut.send_cmd("iperf3 -v")
    dut.expect("cJSON")
