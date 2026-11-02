# Devices in Maurerle Lab

![Photograph of the bench. TODO: add photo once racked/VM in place.](maurerlelab.png)

## Coordinator/Exporter
- Debian VM (2 NICs)
    - NIC 1 (eth0): Uplink (home LAN / internet, WireGuard to coordinator)
    - NIC 2 (labbr0): bridge, connected to LAN port 1 of the AP-32x
      (dnsmasq + TFTP for flashing — TODO)
    - USB: FTDI FT232R serial adapter, connected to console of the AP-32x
    - pdudaemon controls the smart plug over the (lab) VLAN

## DUT
- Aruba AP-32x
    - Console: RS232 115200 8N1 via FT232R (3.3V level on the AP side)
    - LAN Port 1: lab bridge NIC of the VM (TFTP/dnsmasq — TODO)
    - Power: Shelly Plug S running Tasmota 15.x (driver `tasmota` in pdudaemon)

## Misc Hardware
- Shelly Plug S (Tasmota, hostname "NachtLucht")
