# Devices in aparcars Testlab

## Setup

### Coordinator/Exporter

- **Model:** Raspberry Pi 5
- **IP:** `192.168.128.1`
- **Power control:** `pdudaemon` runs on the exporter (`localhost:16421`) and
  toggles PoE on the switch below; each device's `PDUDaemonPort.index` is the
  switch port that powers it.

### Switch (PDU)

- **Model:** Zyxel GS1900-24EP (PoE)
- **IP:** `192.168.128.2`

## Devices

Derived from `ansible/files/exporter/labgrid-aparcar/exporter.yaml`. Each row is
a labgrid place named `labgrid-aparcar-<device>`.

| Device                 | PoE port | Mgmt VLAN | Serial `ID_PATH`                       | Firmware delivery          |
| ---------------------- | -------- | --------- | -------------------------------------- | -------------------------- |
| OpenWrt One            | 1        | 102       | `platform-xhci-hcd.0-usb-0:1.1.1:1.0`  | TFTP (local)               |
| BananaPi BPi-R4-Lite   | 3        | 104       | `platform-xhci-hcd.0-usb-0:1.1.2:1.0`  | TFTP (local)               |
| Genexis Pulse EX400    | 5        | 106       | `platform-xhci-hcd.1-usb-0:1.1.3:1.0`  | TFTP (local)               |
| TP-Link TL-WDR3600 v1  | 7        | 108       | `platform-xhci-hcd.1-usb-0:1.1.1:1.0`  | TFTP via `192.168.105.1`   |
| BananaPi BPi-R4        | 9        | 110       | `platform-xhci-hcd.1-usb-0:1.1.2:1.0`  | TFTP (local)               |
| Raspberry Pi 4         | 11       | 111       | `platform-xhci-hcd.1-usb-0:1.1.4:1.0`  | SD-Mux (`USBSDMuxDevice`)  |
| Enterasys WS-AP3710i   | 12       | 112       | `platform-xhci-hcd.1-usb-0:2.4:1.0`    | TFTP (local)               |
| IgniteNet SS-W2-AC2600 | 13       | 114       | `platform-xhci-hcd.1-usb-0:2.3:1.0`    | TFTP (local)               |

Notes:

- **Firmware delivery** — most devices are flashed over TFTP from the exporter's
  local `/srv/tftp/<device>/`. TP-Link TL-WDR3600 v1 instead pulls from an
  external TFTP server (`external: tftp://192.168.105.1/…`), and the Raspberry
  Pi 4 has no TFTP provider — it is imaged by switching its SD card to the host
  via the `USBSDMuxDevice`.
- **Mgmt VLAN** — the `NetworkService` reaches each device at
  `192.168.1.1%vlan<VLAN>` as `root`.
- Which of these are exercised by CI is controlled by the `devices:` list for
  `labgrid-aparcar` in `labnet.yaml` (not by this file).
