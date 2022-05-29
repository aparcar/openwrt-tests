import time
import pexpect
import sys

def configure_network(dut):
    # Configures the LAN network to DHCP client mode
    # OpenWrt will get an IP address from the qemu DHCP server
    dut.send_cmd("ip addr", "192.168.1.1/24")
    dut.send_cmd("uci set network.lan.proto=dhcp")
    dut.send_cmd("uci changes", "network.lan.proto='dhcp'")
    dut.send_cmd("uci commit")
    dut.send_cmd("service network reload", "entered forwarding state")
    # Wait for DHCP configuration
    time.sleep(8)

    dut.send_cmd("ip addr", "10.0.2.15/24")

def test_opkg(dut):
    configure_network(dut)
    # Wait till network is really available
    # Todo try to find a better way to detect that the WAN is working.
    time.sleep(5)

    # Login to the OpenWrt running in qemu over SSH from the host
    cmd = "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@localhost -p 8022"
    print(f"\nlogin with: {cmd}")
    ssh = pexpect.spawn(cmd, logfile=sys.stdout.buffer)

    ssh.expect("W I R E L E S S   F R E E D O M")
    ssh.expect("root@OpenWrt:~# ")
    ssh.sendline("uname -a")
    ssh.expect("root@OpenWrt:~# ")
    ssh.sendline("exit")
    ssh.expect("Connection to localhost closed.")
    ssh.wait()
