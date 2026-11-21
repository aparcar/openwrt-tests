# labgrid-switchlab

Realtek switches as DUTs. Lab name on the CI server: `switchlab-lab`.

## Setup

| What | Where |
|---|---|
| Lab host | `labgrid-switchlab` (netbird), `br0` = 192.168.128.1 (`eno2` + `eno6np1`), `vlan_interface: br0` |
| Lab switch | Zyxel XS1930-10, OpenWrt, 192.168.128.2. lan1: uplink to the lab host (VLAN 1 untagged, DUT VLANs tagged); lan2..lan10: VLAN 102..110 untagged. Set up with `contrib/labgrid-switch/install-switch.sh --first-vlan 101 --ports 10 --trunk lan1` |
| Power | NETIO 4KF "PowerBOX-RACK", 192.168.128.3, outlets named after the DUTs. JSON API (M2M, user `netio`, password in `host_vars`) through pdudaemon's `netiojson` driver; telnet KSHELL on port 23 doesn't accept that login |

| DUT | Outlet | Serial (USB hub port) | XS1930 port | VLAN | Bootloader |
|---|---|---|---|---|---|
| Linksys LGS328MPC v2 | 4 | ttyUSB0 (1.2) | lan2 (copper) | 102 | U-Boot 2011.12 (Realtek SDK), 115200, `Enter correct key to stop autoboot` (`acp`) |
| XikeStor SKS8300-12X V1 | 2 | ttyUSB1 (1.3) | lan9 (10G SFP+; SKS port 1, FLEXOPTIX 10GBase-R) | 109 | vendor "U-boot version: V2.00", **9600**, `Hit Ctrl+B to stop autoboot` (1 s); OpenWrt at 115200 |
| Ubiquiti USW Aggregation | 3 | ttyUSB2 (1.4) | lan10 (10G SFP+, LR fibre to USW lan1) | 110 | U-Boot usw-US_v1.0.25.77, 115200, `Hit Esc key to stop autoboot` |

All three have OpenWrt (snapshot) in flash and answer on 192.168.1.1 through
their VLAN. Outlet 1 is unused.

## Status (2026-11-21)

Health checks use the snapshot initramfs (none of the three is in a release).

- **SKS8300**: works. `test_base.py` passes from a local run (snapshot
  r38509): the strategy's `console_unlock` gets through the vendor CLI
  (Ctrl+B, menu, Ctrl+F, password), `SmallUBootDriver` (this U-Boot has no
  `echo`) sends `debug_unish_env` at `Boot# ` and runs the init commands at
  `RTL9310# `, TFTP runs over SFP+ port 1 (10G), and the console switches
  from 9600 (`uboot_baudrate`) to the exporter's 115200 at U-Boot's
  `Booting kernel` line. The vendor CLI's `baudrate 115200` (and
  `writeconfig`) doesn't survive a reboot, and this U-Boot has no
  `saveenv` (see the DTS in openwrt/openwrt commit 81b148fd38), so the
  switch stays in the strategy.
- **USW Aggregation**: no console. Power works (NETIO outlet 3 draws ~4 W),
  but ttyUSB2 gets no byte at all through a power cycle: TX not wired or
  TX/RX swapped. Tagged `maintenance=serial`. The U-Boot prompt in the
  target file is still a guess.
- **LGS328MPC**: console carries garbage (ttyUSB0). With the LGS
  unpowered, ttyUSB0 still receives noise whenever the USW powers up, so
  check grounding/wiring of both adapters together. Tagged
  `maintenance=serial`.

The tags come from `maintenance:` of the lab in `labnet.yaml`: a deploy
regenerates the coordinator's places, so a tag set with `labgrid-client
set-tags` only lasts until the next one.

## Next steps

1. Fix the USW and LGS serial wiring. Then USW: fix the U-Boot prompt and
   load address from its console; LGS: check the `acp` interrupt and the
   U-Boot prompt. Drop each from `maintenance:` in `labnet.yaml` once it
   passes its health check, and deploy (`lab.yml --limit labgrid-switchlab
   --tags export`).
