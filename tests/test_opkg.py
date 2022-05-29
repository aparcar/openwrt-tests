import time

def test_opkg(dut):
    # Wait till network is really available
    # Todo try to find a better way to detect that the WAN is working.
    time.sleep(5)

    dut.send_cmd("opkg update", "Signature check passed.")

    dut.send_cmd("opkg list-installed", "procd ")

    dut.send_cmd("opkg install iperf3", "Configuring iperf3")

    dut.send_cmd("opkg list-installed", "iperf3 ")

    dut.send_cmd("iperf3 -v", "cJSON")
