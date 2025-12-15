# Devices in LeineLab Testlab

## Coordinator/Exporter

### Raspberry Pi 5

- Ethernet (eth0): Connection to Switch
- Hardware serial: Not connected
- GPIO: Not connected
- USB: Connected to USB Serial converters and a USB hub

## Switch

### Zyxel GS1900-10HP Switch

- Ethernet Ports:
  - Port 1 (eth): Connected to managed switch untagged VLAN 101
  - Port 2 (eth): Connected to managed switch untagged VLAN 102
  - Port 3 (eth): Connected to managed switch untagged VLAN 103
  - Port 4 (eth): Connected to managed switch untagged VLAN 104
  - Port 5 (eth): Connected to managed switch untagged VLAN 105
  - Port 6 (eth): Connected to managed switch untagged VLAN 106
  - Port 7 (eth): Connected to managed switch untagged VLAN 107
  - Port 8 (eth): Connected to managed switch untagged VLAN 108
  - Port 9 (sfp): Connected to unmanaged switch untagged VLAN 200
  - Port 10 (sfp): Connected to Raspberry Pi 5

## DUT

### genexis_pulse-ex400

- WAN Port: Connected to unmanaged switch
- LAN-Port 1: Connected to managed switch VLAN 101

### tplink_tl-wdr3600-v1

- WAN Port: Connected to unmanaged switch
- LAN-Port 1: Connected to managed switch VLAN 102

### openwrt_one

- WAN Port: Connected to managed switch VLAN 103
- LAN-Port 1: Connected to managed switch VLAN 104

### bananapi_bpi-r4

- WAN Port: Connected to unmanaged switch
- LAN-Port 1: Connected to managed switch VLAN 105

### glinet_gl-mt6000

- WAN Port: Connected to unmanaged switch
- LAN-Port 1: Connected to managed switch VLAN 107

### rpi-4

- LAN-Port 1: Connected to managed switch VLAN 108
