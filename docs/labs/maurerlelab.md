# Devices in Maurerle Lab

## Coordinator/Exporter
- Debian VM "openwrt-tests"
    - eth0: home LAN uplink 192.168.177.199/24 (WAN side, no lab traffic)
    - enxa0cec8874b3b: USB ethernet adapter, the DUT link; carries
      192.168.101.1/24 (the vlan101 subnet, see below)
    - USB: FTDI FT232R serial adapter, connected to console of the AP-32x
    - pdudaemon controls the smart plug over the home LAN
- Runs the labgrid coordinator + exporter pair as all other labs.

### Networking notes (single-NIC host without VLAN trunking)
The home router does not carry 802.1Q, and the DUT cannot tag frames, so
lab VLANs have no L2 path here. Instead the host endpoint of the DUT's
subnet sits directly on the USB NIC (`/etc/netplan/90-dut.yaml`:
192.168.101.1/24), dnsmasq serves DHCP (fixed lease 20:a8:b9:c0:37:64 ->
192.168.101.2) and TFTP from /srv/tftp on that interface, matching the
`external: tftp://192.168.101.1/...` of the exporter.

Two apboot-specific gotchas, both relevant for `external:` and staging:
- apboot's `tftpboot` always DHCPes and ignores `setenv ipaddr`, so the
  TFTP server address must be reachable from the DHCP-assigned address.
- apboot silently drops UDP packets with bad checksums; virtual NIC TX
  checksum offload must be disabled (`ethtool -K <if> tx off`).
Boot images live under /srv/tftp/ because dnsmasq's built-in TFTP runs as
nobody and must be able to follow the staging symlinks.

## DUT
- Aruba AP-32x
    - Console: RS232 115200 8N1 via FT232R (3.3V level on the AP side)
    - LAN Port: plugged into the USB ethernet adapter above
    - Power: Shelly Plug S running Tasmota 15.x (driver `tasmota` in
      pdudaemon, needs pdudaemon/pdudaemon#242)
    - TFTP-booted SNAPSHOT initramfs (RAM only); the installed Freifunk
      firmware stays untouched while testing
    - Caveat: SNAPSHOT initramfs has firewall4 `input policy drop` on
      wan, which is the only physical port, so the suite's ssh setup
      needs the lab's local workaround (a small SSHDriver subclass that
      stops+disables the firewall over serial before connecting).

## Misc Hardware
- Shelly Plug S (Tasmota, hostname "NachtLucht")
