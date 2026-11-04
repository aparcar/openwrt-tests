# Devices in HSN Testlab

## Setup

- **Exporter:** Raspberry pi 5
- **Labgrid IP:** `192.168.128.1`

- **Model:** Zyxel GS1900-10HP A1
- **Switch 1 IP:** `192.168.128.2`

- **Model:** Zyxel GS1900-10HP A1
- **Switch 2 IP:** `192.168.128.3`

- **Model:** Zyxel GS1900-24HP
- **Power Outlet 2 IP:** `192.168.128.4`

## Switch 1

- **Model:** Zyxel GS1900-10HP A1
- **Switch IP:** `192.168.128.2`

| Port  | PoE         | Device                                   | Serial port   | GPIO Pwr  | Vlan #   |
| ----- | ----------- | ---------------------------------------- | ------------- | --------- | -------- |
| 1     | ⬜ Inactive | GL.iNet GL-MT6000 WAN (Raspberry GPIO)   | ttyCH9344USB0 | Pin 2     | Vlan 101 |
| 2     | ⬜ Inactive | .. LAN                                   |               |           | Vlan 102 |
| 3     | ⬜ Inactive | Bananapi bpi-r4 WAN (Raspberry GPIO)     | ttyCH9344USB1 | Pin 4     | Vlan 103 |
| 4     | ⬜ Inactive | .. LAN                                   |               |           | Vlan 104 |
| 5     | ⬜ Inactive | Teltonika RUT976 WAN (Raspberry GPIO)    | ttyCH9344USB2 | Pin 24    | Vlan 105 |
| 6     | ⬜ Inactive | .. LAN                                   |               |           | Vlan 106 |
| 7     | ⬜ Inactive | Lumen W1700K UBI (Raspberry GPIO)        | ttyCH9344USB3 | Pin 3     | Vlan 107 |
| 8     | ⬜ Inactive | .. LAN                                   |               |           | Vlan 108 |
| 9     | ⬜ Inactive | Switch 2 Zyxel GS1900-10HP A1            |               |           | Vlan 1   |
| 10    | ⬜ Inactive | Rasmberry Pi 5                           |               |           | Vlan 1   |




## Switch 2

- **Model:** Zyxel GS1900-10HP A1
- **Switch IP:** `192.168.128.3`

| Port  | PoE         | Device                                   | Serial port   | GPIO Pwr  | Vlan #   |
| ----- | ----------- | ---------------------------------------- | ------------- | --------- | -------- |
| 1     | ⬜ Inactive | D-Link DAP-X1860 A1 WAN (Raspberry GPIO) | ttyCH9344USB4 | Pin 9     | Vlan 109 |
| 2     | 🟩 Active   | Aruba AP_325 WAN (PoE)                   | ttyUSB1       |           | Vlan 110 |
| 3     | 🟩 Active   | Enterasys WS-AP3710i WAN (PoE)           | ttyUSB4       |           | Vlan 111 |
| 4     | 🟩 Active   | Unifi U7-Pro-XGS WAN (PoE)               | ttyCH9344USB5 |           | Vlan 112 |
| 5     | ⬜ Inactive | TP-Link RE650 v1 WAN (Raspberry GPIO)    | ttyCH9344USB6 | Pin 23    | Vlan 113 |
| 6     | ⬜ Inactive |                                          |               |           | Vlan 114 |
| 7     | ⬜ Inactive |                                          |               |           | Vlan 115 |
| 8     | ⬜ Inactive |                                          |               |           | Vlan 116 |
| 9     | ⬜ Inactive |                                          |               |           | Vlan 1   |
| 10    | ⬜ Inactive | Rasmberry Pi 5                           |               |           | Vlan 1   |



## Switch 3

- **Model:** Zyxel GS1900-24HP
- **Switch IP:** `192.168.128.4`

| Port  | PoE         | Device                                   | Serial port   | GPIO Pwr  | Vlan #   |
| ----- | ----------- | ---------------------------------------- | ------------- | --------- | -------- |
| 1     | 🟩 Active   | TpLink TL-WR1043nd-v4 (PoE TP-Link)      | ttyCH9344USB8 |           | Vlan 117 |
| 2     | ⬜ Inactive | .. LAN                                   |               |           | Vlan 118 |
| 3     | 🟩 Active   | Genexis pulse-ex400 WAN (PoE TP-Link)    | ttyCH9344USB9 |           | Vlan 119 |
| 4     | ⬜ Inactive | .. LAN                                   |               |           | Vlan 120 |
| 5     | 🟩 Active   | Linksys EA8450 (PoE TP-Link)             | ttyCH9344USB10|           | Vlan 121 |
| 6     | ⬜ Inactive | .. LAN                                   |               |           | Vlan 122 |
| 7     | 🟩 Active   | Bananapi bpi-r64 WAN (PoE direct)        | ttyCH9344USB11|           | Vlan 123 |
| 8     | ⬜ Inactive | .. LAN                                   |               |           | Vlan 124 |
| 9-25  | ⬜ Inactive | ...                                      |               |           | Vlan ... |
| 26.   | ⬜ Inactive | Raspberry pi 5                           |               |           | Vlan 1   |
