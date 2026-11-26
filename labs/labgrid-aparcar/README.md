# Devices in aparcars Testlab

## Setup

### Coordinator/Exporter

- **Model:** Raspberry Pi 5
- **IP:** `172.20.0.1`
- **Power control:** `pdudaemon` runs on the exporter (`localhost:16421`) and
  toggles PoE on the switches below via its `ubus` driver; each device's
  `PDUDaemonPort.pdu` is the switch (`labgrid-switch-1` to `labgrid-switch-3`)
  and `.index` the port that powers it
  (`labs/labgrid-aparcar/pdudaemon.conf`). The names resolve
  through the exporter's `/etc/hosts` (`labgrid_hosts` in
  `ansible/host_vars/labgrid-aparcar.yml`).

### Management network

`172.20.0.0/24`, untagged on the trunks. It isn't `192.168.128.0/24` (as in
other labs) because DUT VLAN 128 needs `192.168.128.0/24` on the exporter.

| Address      | Device                                  |
| ------------ | --------------------------------------- |
| `172.20.0.1` | Exporter                                |
| `172.20.0.2` | `labgrid-switch-1` (Linksys LGS328MPC)  |
| `172.20.0.3` | labgrid-probe                           |
| `172.20.0.4` | Wi-Fi probe                             |
| `172.20.0.5` | Wi-Fi probe 2                           |
| `172.20.0.6` | `labgrid-switch-2` (Plasma Cloud PSX10) |
| `172.20.0.7` | `labgrid-switch-3` (ZyXEL GS1900-24EP)  |

### Switch 1 (PDU)

- **Model:** Linksys LGS328MPC v2 (PoE, running OpenWrt)
- **IP:** `172.20.0.2`
- **DUT ports:** `lan1`–`lan23`, VLAN 101–123. `lan24` is the uplink of
  switch 3.

### Switch 2 (PDU)

- **Model:** Plasma Cloud PSX10 (PoE, running OpenWrt)
- **IP:** `172.20.0.6`
- **DUT ports:** `lan1`–`lan8`, VLAN 125–132. `lan9`/`lan10` are SFP+.

### Switch 3 (PDU)

- **Model:** ZyXEL GS1900-24EP (802.3af PoE on `lan1`–`lan12`, at most
  15.4 W per port, running OpenWrt with the kernel PSE driver; the `poe` ubus
  object comes from rpcd-mod-ethtool-poe, see below)
- **IP:** `172.20.0.7`
- **DUT ports:** `lan1`–`lan23`, VLAN 133–155. `lan24` is the uplink to
  switch 1 `lan24`.

All switches are set up with `contrib/labgrid-switch/install-switch.sh`
(VLANs, trunks, management address, hostname):

```shell
sh contrib/labgrid-switch/install-switch.sh --name labgrid-switch-1 \
    --address 172.20.0.2/24 --gateway 172.20.0.1 \
    --first-vlan 101 --ports 24 --transit 125-155 \
    --trunk lan24 --trunk lan25 --trunk lan26 --trunk lan27 --trunk lan28 \
    root@172.20.0.2 -J labgrid-aparcar

sh contrib/labgrid-switch/install-switch.sh --name labgrid-switch-2 \
    --address 172.20.0.6/24 --gateway 172.20.0.1 \
    --first-vlan 125 --ports 8 --trunk lan9 \
    root@172.20.0.6 -J labgrid-aparcar

sh contrib/labgrid-switch/install-switch.sh --name labgrid-switch-3 \
    --address 172.20.0.7/24 --gateway 172.20.0.1 \
    --first-vlan 133 --ports 24 --trunk lan24 \
    root@172.20.0.7 -J labgrid-aparcar
```

Switch 1's transit range covers switch 2 (125–132) and switch 3 (133–155):
the script takes one range, and its trunks carry both switches' VLANs to the
exporter and the probe.

It writes the config without applying it; `/etc/init.d/network restart` on
the switch does.

### Power strip (PDU)

- **Model:** Shelly Power Strip 4 Gen4 (`ShellyPStripG4-D885ACEBBCB0`), for
  devices that can't be powered over PoE
- **pdudaemon name:** `labgrid-strip-1`, driver `shelly_gen2`: JSON-RPC over
  HTTP to the strip at `192.168.8.214` (Wi-Fi). The driver connects to the
  PDU's name, so `labgrid_hosts` in the host's `ansible/host_vars` maps
  `labgrid-strip-1` to that address. Bluetooth LE (`shelly_ble`) dropped
  every connection during service discovery (HCI reason 0x3e).
- **Outlets:** `PDUDaemonPort.index` 0–3, so index 3 is the fourth socket.
  Index 3 powers the UniFi Cloud Gateway Fiber (`ubnt_ucgfiber`, bring-up
  only: no published image yet, so its healthcheck fails and CI schedules
  nothing on it). Index 2 powers the ZyXEL GS1900-8HP B1
  (`zyxel_gs1900-8hp-b1`, in maintenance: kselftest loops, see below).
- Authentication must stay disabled on the strip (`shelly_gen2` doesn't
  support it), and it needs a fixed address (DHCP reservation).

### labgrid-probe

- **Model:** GL.iNet GL-MT2500 (`contrib/labgrid-probe`)
- **IP:** `172.20.0.3`
- **labgrid place:** `labgrid-aparcar-probe` (tag `role: probe`). Tests
  reserve and lock it for the test modules that use it.
- Sits on a trunk port with every DUT VLAN tagged. Per DUT it creates one
  network namespace on the LAN VLAN (DHCP client, iperf3 client) and one on
  the WAN VLAN (iperf3 server, DHCP for the DUT's WAN), so
  `tests/test_probe_lan.py` can test DHCP, DNS, SLAAC and LAN→WAN throughput
  through the DUT. Controlled as the `labgrid-probe` ubus object over HTTP;
  see `contrib/labgrid-probe/README.md`.

## Network

Every DUT gets two switch ports, each untagged in its own VLAN:

- **Switch ports:** on switch 1, port `lanN` is the untagged member of VLAN
  `100+N`; on switch 2, of VLAN `124+N`; on switch 3, of VLAN `132+N`.
- **Trunks:**
  - Switch 1 `lan25` and `lan26` carry all DUT VLANs (101–155) tagged, plus
    the management network (VLAN 1) untagged. That's where the exporter and
    the labgrid-probe are connected.
  - Switch 1 `lan28` is the uplink of switch 2 (its `lan9`), switch 1 `lan24`
    the uplink of switch 3 (its `lan24`): the DUT VLANs tagged, management
    untagged.
  - Switch 1 `lan27` is a trunk as well (all DUT VLANs tagged, management
    untagged).
- **LAN VLAN** (the DUT's LAN port):
  - The exporter has `192.168.<VLAN>.1/24` for TFTP and `192.168.1.<VLAN>/24`
    to reach the DUT, which keeps its default `192.168.1.1`.
  - U-Boot gets static addresses: `serverip` is `192.168.<VLAN>.1` and
    `ipaddr` is `.2`, set by the strategy from the `tftp://` URL. It never
    needs DHCP.
  - Once booted, the DUT is the only DHCP server on this segment: the
    exporter's dnsmasq serves TFTP only, no DHCP.
- **WAN VLAN** (the DUT's WAN port, if cabled): the exporter is the upstream
  (`192.168.<VLAN>.1`, IP forwarding). The DUT's WAN lease comes from the
  labgrid-probe while a test has the WAN side up (`wan_up`; range
  `.100`–`.200`, router `.1`); the exporter serves no DHCP. It's
  declared per place as a `wan` resource in `exporter.yaml`:

  ```yaml
  wan:
    cls: NetworkInterface
    ifname: vlan101
  ```

  Tests read it as the place's `RemoteNetworkInterface` named `wan`.

## Wiring

### Network and power

Switch 1 port `lanN` is VLAN `100+N`, switch 2 port `lanN` is VLAN `124+N`,
switch 3 port `lanN` is VLAN `132+N`.
⚡ marks the PoE port that pdudaemon toggles (`PDUDaemonPort.index`, on the
switch named in `PDUDaemonPort.pdu`). The IgniteNet and the Aruba fetch
firmware over their second port (VLAN 113 and 115).

```mermaid
flowchart LR
  EXP["Exporter (Raspberry Pi 5)<br/>172.20.0.1<br/>pdudaemon :16421 · TFTP"]
  PROBE["labgrid-probe (GL-MT2500)<br/>172.20.0.3"]
  SW2["Plasma Cloud PSX10 (PoE, OpenWrt) · 172.20.0.6<br/>lan1–lan8: VLAN 125–132"]
  SW3["ZyXEL GS1900-24EP (PoE, OpenWrt) · 172.20.0.7<br/>lan1–lan23: VLAN 133–155"]

  subgraph SW["Linksys LGS328MPC v2 (PoE, OpenWrt) · 172.20.0.2"]
    T["lan25 / lan26<br/>trunk: VLAN 101–155 tagged<br/>+ mgmt untagged"]
    U["lan28 / lan24<br/>trunk: VLAN 101–155 tagged<br/>+ mgmt untagged"]
  end

  EXP ---|trunk| T
  PROBE ---|trunk| T
  SW2 ---|lan9| U
  SW3 ---|lan24| U

  SW ---|"lan1 WAN ⚡ · lan2 LAN"| d1["OpenWrt One"]
  SW3 ---|"lan1 LAN · lan6 WAN ⚡"| d2["BPi-R4-Lite"]
  SW2 ---|"lan1 LAN ⚡ · lan2 WAN"| d3["Genexis Pulse EX400"]
  SW2 ---|"lan3 LAN ⚡ · lan4 WAN"| d4["TL-WDR3600 v1"]
  SW2 ---|"lan5 LAN ⚡ · lan6 WAN"| d5["BPi-R4"]
  SW2 ---|"lan7 LAN ⚡"| d6["Raspberry Pi 4"]
  SW ---|"lan12 LAN ⚡"| d7["Enterasys WS-AP3710i"]
  SW ---|"lan13 TFTP ⚡ · lan14 LAN"| d8["IgniteNet SS-W2-AC2600"]
  SW ---|"lan15 TFTP ⚡ · lan16 LAN"| d9["Aruba AP-32x"]
  SW3 ---|"lan9 LAN ⚡"| d10["Raspberry Pi 3B"]
  SW3 ---|"lan10 LAN ⚡"| d17["WD My Book Live"]
  SW ---|"lan5 LAN"| d11["ZyXEL GS1900-8HP B1 (lan1)<br/>power: strip outlet 2"]
  SW3 ---|"lan3 WAN · lan4 LAN ⚡"| d12["VisionFive2"]
  SW2 ---|"lan8 LAN ⚡"| d13["RIPE Atlas v5"]
  SW3 ---|"lan7 WAN · lan8 LAN ⚡"| d14["ASUS RT-N16"]
  SW3 ---|"lan2 LAN ⚡"| d15["o2 Box 6431"]
  SW ---|"lan7 LAN · lan8 WAN"| d16["UniFi Cloud Gateway Fiber<br/>power: strip outlet 3"]
```

### USB (serial consoles and SD-Muxes)

Three 7-port hubs, plus hub D on hub A port 1.4 for the devices on switch 3.
Hubs A–C are two cascaded 4-port hub chips each, with the inner chip on port
1. The labels are the `ID_PATH` port numbers (from `lsusb -tv`). Free ports:
hub A `1.1.2`; hub B `1.4`; hub C `2.1.1`–`2.1.4`, `2.2`, `2.4`. Hub D is
full (the devices that moved to switch 3 came with their adapters).

```mermaid
flowchart LR
  subgraph EXP["Exporter (Raspberry Pi 5)"]
    X0["xhci-hcd.0<br/>bus 1 USB2 · bus 2 USB3 (unused)"]
    X1["xhci-hcd.1<br/>bus 3 USB2 · bus 4 USB3"]
  end

  subgraph HUBA["Hub A: 7-port USB2 (2× Huasheng 214b:7250)"]
    A0["1"] --> A1["1.1"]
  end
  subgraph HUBB["Hub B: 7-port USB3 (2× Genesys 05e3:0610/0626)"]
    B0["1"] --> B1["1.1"]
  end
  subgraph HUBC["Hub C: 7-port USB2 (2× Huasheng 214b:7250)"]
    C0["2"] --> C1["2.1"]
  end

  X0 -->|port 1| A0
  X1 -->|port 1| B0
  X1 -->|port 2| C0

  A1 -->|1.1.1| a1["LGT8F328P cdc_acm<br/>OpenWrt One"]
  A1 -->|1.1.3| a7["CH340<br/>Enterasys WS-AP3710i"]
  A1 -->|1.1.4| a8["CH340<br/>IgniteNet SS-W2-AC2600"]
  A0 -->|1.2| a3["CH340<br/>Aruba AP-32x"]
  A0 -->|1.3| a4["CH340<br/>ZyXEL GS1900-8HP B1"]
  A0 -->|1.4| D0["Hub D (switch 3 devices)"]
  D0 -->|1.4.1| D1["1.4.1"]
  D1 -->|1.4.1.1.1| d1m["USB-SD-Mux<br/>Raspberry Pi 3B SD"]
  D1 -->|1.4.1.2| d1s["CH9102 cdc_acm<br/>Raspberry Pi 3B"]
  D1 -->|1.4.1.3| d1o["PL2303<br/>o2 Box 6431"]
  D1 -->|1.4.1.4| d1v["CH9102 cdc_acm<br/>VisionFive2"]
  D0 -->|1.4.2| d2r["LGT8F328P cdc_acm<br/>BPi-R4-Lite"]
  D0 -->|1.4.3| d3a["PL2303<br/>ASUS RT-N16"]
  D0 -->|1.4.4| d4w["serial adapter<br/>WD My Book Live"]

  B1 -->|1.1.1| b1["CP210x<br/>TL-WDR3600 v1"]
  B1 -->|1.1.2| b2["CP210x<br/>BPi-R4"]
  B1 -->|1.1.3| b3["CP210x<br/>Genexis Pulse EX400"]
  B1 -->|1.1.4| b4["CP210x<br/>Raspberry Pi 4"]
  B0 -->|1.2| b5["USB-SD-Mux<br/>Raspberry Pi 4 SD"]
  B0 -->|1.3| b6["PL2303<br/>RIPE Atlas v5"]

  C0 -->|2.3| c5["CH9102 cdc_acm<br/>UniFi Cloud Gateway Fiber"]

  classDef mux fill:#eef,stroke:#669
  class b5,d1m mux
```

The USB-SD-Mux shows up as a `0424:2640` hub with a `0424:4041` card reader on
its port 1, which is the `USBSDMuxDevice` `ID_PATH` (e.g. `…:1.4.1`).

## Devices

Derived from `labs/labgrid-aparcar/exporter.yaml`. Each row is
a labgrid place named `labgrid-aparcar-<device>`. LAN and WAN are
`<VLAN> (<switch port>)`; ⚡ marks the port that powers the device over PoE
(pdudaemon's `PDUDaemonPort.index` is that port's number).

### Switch 1 (`labgrid-switch-1`)

| Device                                       | LAN          | WAN          | Serial `ID_PATH`                      | Firmware delivery                              |
| -------------------------------------------- | ------------ | ------------ | ------------------------------------- | ---------------------------------------------- |
| OpenWrt One                                  | 102 (lan2)   | 101 (lan1 ⚡) | `platform-xhci-hcd.0-usb-0:1.1.1:1.0` | TFTP via `192.168.102.1`                       |
| ZyXEL GS1900-8HP B1 (power: strip outlet 2)  | 105 (lan5)   | –            | `platform-xhci-hcd.0-usb-0:1.3:1.0`   | TFTP via `192.168.105.1` (maintenance)         |
| UniFi Cloud Gateway Fiber (power: strip outlet 3) | 107 (lan7) | 108 (lan8) | `platform-xhci-hcd.1-usb-0:2.3:1.0`   | TFTP via `192.168.107.1` (bring-up only)       |
| Enterasys WS-AP3710i                         | 112 (lan12 ⚡) | –          | `platform-xhci-hcd.0-usb-0:1.1.3:1.0` | TFTP via `192.168.112.1`                       |
| IgniteNet SS-W2-AC2600                       | 114 (lan14)  | –            | `platform-xhci-hcd.0-usb-0:1.1.4:1.0` | TFTP via `192.168.113.1` on 113 (lan13 ⚡)      |
| Aruba AP-32x                                 | 116 (lan16)  | –            | `platform-xhci-hcd.0-usb-0:1.2:1.0`   | TFTP via `192.168.115.1` on 115 (lan15 ⚡)      |

Free ports: `lan3`, `lan4`, `lan6`, `lan9`–`lan11`, `lan17`–`lan23`.

### Switch 3 (`labgrid-switch-3`)

| Device                | LAN            | WAN          | Serial `ID_PATH`                        | Firmware delivery                  |
| --------------------- | -------------- | ------------ | --------------------------------------- | ---------------------------------- |
| BananaPi BPi-R4-Lite  | 133 (lan1)     | 138 (lan6 ⚡) | `platform-xhci-hcd.0-usb-0:1.4.2:1.0`   | TFTP via `192.168.133.1`           |
| o2 Box 6431           | 134 (lan2 ⚡)   | –            | `platform-xhci-hcd.0-usb-0:1.4.1.3:1.0` | TFTP via `192.168.134.1`           |
| StarFive VisionFive2  | 136 (lan4 ⚡)   | 135 (lan3)   | `platform-xhci-hcd.0-usb-0:1.4.1.4:1.0` | TFTP via `192.168.136.1`           |
| ASUS RT-N16           | 140 (lan8 ⚡)   | 139 (lan7)   | `platform-xhci-hcd.0-usb-0:1.4.3:1.0`   | CFE flashes via `192.168.140.1`    |
| Raspberry Pi 3B       | 141 (lan9 ⚡)   | –            | `platform-xhci-hcd.0-usb-0:1.4.1.2:1.0` | SD-Mux (`USBSDMuxDevice`)          |
| WD My Book Live       | 142 (lan10 ⚡)  | –            | `platform-xhci-hcd.0-usb-0:1.4.4:1.0`   | TFTP via `192.168.142.1`           |

Free ports: `lan5`, `lan11`–`lan23` (PoE up to `lan12`). `lan1`'s PoE output
is broken: it detects and classifies a powered device but never switches
power on, with several devices and both realtek-poe and the kernel driver.
So it carries the R4-Lite's LAN, which needs no PoE.

PoE: the 24EP's PoE controller is a Gen1 MCU (Broadcom dialect, firmware
v16.1) with two BCM59011 PSE chips (id `0xe011`). Switch 3 runs a self-built
image with the in-kernel realtek-pse-mcu driver (it needs the BCM59011 id,
not in OpenWrt yet) and rpcd-mod-ethtool-poe for the `poe` ubus object, so
pdudaemon's `ubus` driver works unchanged. A stock OpenWrt image brings back
realtek-poe, and sysupgrade drops `/usr/share/rpcd/acl.d/pdudaemon.json`:
run `install-switch.sh` again after flashing one. The PSE inputs are in port
order (`lanN` is PSE port N-1); `ethtool --show-pse lanN` shows a port's
state, `ethtool --set-pse lanN c33-pse-admin-control enable|disable` or
`ubus call poe manage '{"port":"lanN","enable":true}'` switches it. The
budget is 130 W, at most 15.4 W per port (802.3af). The PoE controller keeps
each port's on/off state across a reboot of the switch.

### Switch 2 (`labgrid-switch-2`)

| Device                | LAN          | WAN        | Serial `ID_PATH`                      | Firmware delivery         |
| --------------------- | ------------ | ---------- | ------------------------------------- | ------------------------- |
| Genexis Pulse EX400   | 125 (lan1 ⚡) | 126 (lan2) | `platform-xhci-hcd.1-usb-0:1.1.3:1.0` | TFTP via `192.168.125.1`  |
| TP-Link TL-WDR3600 v1 | 127 (lan3 ⚡) | 128 (lan4) | `platform-xhci-hcd.1-usb-0:1.1.1:1.0` | TFTP via `192.168.127.1`  |
| BananaPi BPi-R4       | 129 (lan5 ⚡) | 130 (lan6) | `platform-xhci-hcd.1-usb-0:1.1.2:1.0` | TFTP via `192.168.129.1`  |
| Raspberry Pi 4        | 131 (lan7 ⚡) | –          | `platform-xhci-hcd.1-usb-0:1.1.4:1.0` | SD-Mux (`USBSDMuxDevice`) |
| RIPE Atlas v5         | 132 (lan8 ⚡) | –          | `platform-xhci-hcd.1-usb-0:1.3:1.0`   | TFTP via `192.168.132.1`  |

No free ports.

Notes:

- **Firmware delivery:**
  - TFTP devices are flashed from the exporter's `/srv/tftp/<device>/`. Their
    `TFTPProvider.external` is a `tftp://192.168.<VLAN>.1/<device>/` URL.
  - The IgniteNet and the Aruba fetch over their second port, so their TFTP
    VLAN (113, 115) differs from their LAN VLAN.
  - The Raspberry Pis have no TFTP provider: they are
    imaged by switching their SD card to the host via a `USBSDMuxDevice`.
  - The ASUS RT-N16 has CFE instead of U-Boot and no netbootable image:
    `CFETFTPStrategy` interrupts CFE, gives it `192.168.140.2` and writes the
    TRX to flash over TFTP on every run (several minutes).
  - The o2 Box 6431's U-Boot (2013.10-openwrt4) has no `echo`
    (`SmallUBootDriver`); it loads the initramfs to `0x82000000` and boots it
    from RAM.
- **WAN VLAN:** "–" means the board has no WAN port, or it isn't cabled. The
  WAN VLANs were identified from the WAN MAC (LAN MAC ±1) in the exporter's
  DHCP leases.
- **Serial:** the Enterasys, IgniteNet, Aruba AP-32x, o2 Box 6431, RIPE
  Atlas v5, ASUS RT-N16 and GS1900-8HP additionally match `@port_number: 0`.
- **GS1900-8HP B1:** loop cables lan5↔lan6 and lan7↔lan8 for the kernel's DSA
  selftests. Its stock U-Boot enables all ports with `rtk network on`, so
  the target file stops RX/TX on lan5–8 there (`mw.l` on `MAC_PORT_CTRL`,
  not saved) before TFTP. It stays in `maintenance:`: an
  official image would bridge the loops; only self-built kselftest images
  keep lan5–8 out of the bridge. All consoles run at the default 115200
  baud.
- **LAN access:** the `NetworkService` reaches each device at
  `192.168.1.1%vlan<LAN VLAN>` as `root`.
- The devices the lab offers (its places' `device=` tags) come from the
  `devices:` list in `labs/labgrid-aparcar/lab.yaml`, not from this file.

## Status (2026-11-26)

In `maintenance:` of `lab.yaml` (CI skips them):

- **o2 Box 6431:** unstable on its PoE splitter power (random LZMA unpack
  failures in U-Boot, oopses, garbled UART input, TFTP frame errors); next
  step is its original power supply.
- **Raspberry Pi 3B:** new on switch 3; its SD-Mux reports no medium
  (card missing or not seated).
- **ZyXEL GS1900-8HP B1:** kselftest loop cables, self-built images only.

Gone: the Pine64 Rock64 (switch 3's 802.3af ports couldn't power it) and the
UniFi Security Gateway (not wired, see its FIXME in `exporter.yaml`).
