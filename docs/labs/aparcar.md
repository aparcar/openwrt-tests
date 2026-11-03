# Devices in aparcars Testlab

## Setup

### Coordinator/Exporter

- **Model:** Raspberry Pi 5
- **IP:** `192.168.128.1`
- **Power control:** `pdudaemon` runs on the exporter (`localhost:16421`) and
  toggles PoE on the switch below via its `ubus` driver; each device's
  `PDUDaemonPort.index` is the switch port that powers it.

### Switch (PDU)

- **Model:** Linksys LGS328MPC v2 (PoE, running OpenWrt)
- **IP:** `192.168.128.2`

## Devices

Derived from `ansible/files/exporter/labgrid-aparcar/exporter.yaml`. Each row is
a labgrid place named `labgrid-aparcar-<device>`.

| Device                 | PoE port | VLAN | Serial `ID_PATH`                      | Firmware delivery              |
| ---------------------- | -------- | ---- | ------------------------------------- | ------------------------------ |
| OpenWrt One            | 1        | 102  | `platform-xhci-hcd.0-usb-0:1.1.1:1.0` | TFTP (local)                   |
| BananaPi BPi-R4-Lite   | 3        | 104  | `platform-xhci-hcd.0-usb-0:1.1.2:1.0` | TFTP (local)                   |
| Genexis Pulse EX400    | 5        | 105  | `platform-xhci-hcd.1-usb-0:1.1.3:1.0` | TFTP (local)                   |
| TP-Link TL-WDR3600 v1  | 7        | 107  | `platform-xhci-hcd.1-usb-0:1.1.1:1.0` | TFTP via `192.168.107.1`       |
| BananaPi BPi-R4        | 9        | 109  | `platform-xhci-hcd.1-usb-0:1.1.2:1.0` | TFTP (local)                   |
| Raspberry Pi 4         | 11       | 111  | `platform-xhci-hcd.1-usb-0:1.1.4:1.0` | SD-Mux (`USBSDMuxDevice`)      |
| Enterasys WS-AP3710i   | 12       | 112  | `platform-xhci-hcd.1-usb-0:2.4:1.0`   | TFTP via `192.168.112.1`       |
| IgniteNet SS-W2-AC2600 | 13       | 114  | `platform-xhci-hcd.1-usb-0:2.3:1.0`   | TFTP via `192.168.113.1`       |
| Aruba AP-32x           | 15       | 116  | `platform-xhci-hcd.1-usb-0:2.2:1.0`   | TFTP via `192.168.115.1`       |
| Pine64 Rock64          | 17       | 117  | `platform-xhci-hcd.1-usb-0:2.1.3:1.0` | SD-Mux (`USBSDMuxDevice`)      |
| StarFive VisionFive2   | 19       | 119  | `platform-xhci-hcd.1-usb-0:2.1.2:1.0` | TFTP (local)                   |

Notes:

- **Firmware delivery:** most devices are flashed over TFTP from the exporter's
  `/srv/tftp/<device>/`. Where the table names an address, the device fetches
  the firmware from that address (`TFTPProvider.external`) instead of the
  default TFTP server. The Raspberry Pi 4 and Pine64 Rock64 have no TFTP
  provider: they are imaged by switching their SD card to the host via a
  `USBSDMuxDevice`.
- **Serial:** the Aruba AP-32x and Pine64 Rock64 additionally match
  `@port_number: 0`. The Pine64 Rock64 console runs at 1500000 baud, all
  others at the default 115200.
- **VLAN:** the `NetworkService` reaches each device at
  `192.168.1.1%vlan<VLAN>` as `root`.
- The devices the lab offers (its places' `device=` tags) come from the
  `devices:` list for `labgrid-aparcar` in `labnet.yaml`, not from this file.
