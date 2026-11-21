# Devices in FCEFYN Lab

## Coordinator/Exporter

- Ubuntu Host
    - Ethernet: VLAN trunk to switch
    - USB serial adapters via hub (udev symlinks)
    - Arduino relay control (barrel-jack DUTs)
    - PoE switch control (PoE DUTs via TP-Link SG2016P)
    - TFTP/DHCP: dnsmasq per VLAN

## Switch

- **Model:** TP-Link SG2016P (16-port Gigabit, 8 PoE)

| Port | Device           | Power |
|------|------------------|-------|
| 1    | OpenWRT One      | PoE   |
| 2    | LibreRouter #1   | Relay |
| 3    | LibreRouter #2   | Relay |
| 9    | Host             | Trunk |
| 10   | MikroTik         | Trunk |
| 11   | Belkin RT3200 #1 | Relay |
| 12   | Belkin RT3200 #2 | Relay |
| 13   | Belkin RT3200 #3 | Relay |
| 14   | Banana Pi R4     | Relay |

## DUTs

- **Belkin RT3200 #1, #2, #3** (linksys_e8450)
    - Power: Arduino relay (channels 0–2)
    - Target: mediatek-mt7622

- **Banana Pi R4** (bananapi_bpi-r4)
    - Power: Arduino relay (channel 3)
    - Target: mediatek-filogic

- **OpenWrt One** (openwrt_one)
    - Power: PoE (switch port 1)
    - Target: mediatek-filogic

- **LibreRouter #1** (librerouter_librerouter-v1)
    - Power: Arduino relay (channel 4, 12V DC jack)
    - Target: ath79-generic

## Maintainers

- @javierbrk
- @francoriba
- @ccasanueva7

## Location

- Universidad Nacional de Córdoba. Facultad de Ciencias Exactas, Físicas y Naturales
- Córdoba, Argentina
