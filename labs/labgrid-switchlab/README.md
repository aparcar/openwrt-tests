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

The tags come from `maintenance:` of the lab in `labs/labgrid-switchlab/lab.yaml`: a deploy
regenerates the coordinator's places, so a tag set with `labgrid-client
set-tags` only lasts until the next one.

## Switch tests

`tests/test_switch.py` checks what the uplink alone shows (ports in the
bridge, uplink errors, SFP module). `tests/test_switch_probe.py` tests the
switching itself: flooding, MAC learning, VLAN separation, hardware
forwarding (iperf3 at line rate while the DUT's CPU sees nothing) and link
flaps. Its hosts are VLAN interfaces of the lab host, run by dprobe
(`dprobe l2`, contrib/debian-probe) on the lab host:

```
DUT port A ── XS1930 lanX (VLAN 10X) ─┐
DUT port B ── XS1930 lanY (VLAN 10Y) ─┼─ lan1 trunk ── lab host vlan10X / vlan10Y / uplink VLAN
DUT uplink ── XS1930 (DUT's VLAN) ────┘                (dprobe l2 frames / iperf)
```

The XS1930 keeps the VLANs apart, so the three hosts only meet through the
DUT's bridge. Frames are raw Ethernet (ethertype 0x88b5) and counted per
host; iperf3 runs between throwaway namespaces with macvlans on two VLAN
interfaces. `dprobe l2 link` takes a test port of the XS1930 down/up over
SSH, only the ports listed in `dprobe_lab_switch.ports`.

Cabling (lan3–lan8 are already VLANs 103–108 on the XS1930):

| DUT | A | B | XS1930 |
|---|---|---|---|
| LGS328MPC | lan3 | lan4 | lan3, lan4 (copper) |
| SKS8300 | port 2 | port 3 | lan5, lan6 (10GBase-T SFP+ modules) |
| USW Aggregation | lan2 | lan3 | lan7, lan8 (10GBase-T SFP+ modules) |

Which DUT port is A or B doesn't matter: the fixture finds out by sending
frames and watching the DUT's port counters. Tagged traffic can't pass the
XS1930's access ports, so tests of 802.1Q tags, LACP and STP need direct
links (a lab host SFP+ NIC).

Setup:

1. Cable as above.
2. Install dprobe on the lab host (contrib/debian-probe/ansible, the
   `debian_probes` group) with
   `dprobe_allowed_vlans: ["103-110"]` (developers log in with the GitHub
   keys of the host's `labgrid_access`, the local labgrid-agent with its
   own key), and
   `dprobe_lab_switch: {host: root@192.168.128.2, key:
   /etc/dprobe/lab-switch.key, vlan_offset: 100, ports: [lan3, lan4, lan5,
   lan6, lan7, lan8]}`. Generate that key on the lab host and add its
   public half to the XS1930's `/etc/dropbear/authorized_keys`.
3. Add the probe hosts to each DUT's place in `exporter.yaml`, e.g. for the
   SKS8300:

   ```yaml
     switch-a:
       cls: NetworkInterface
       ifname: vlan105
     switch-b:
       cls: NetworkInterface
       ifname: vlan106
   ```

   and deploy (`lab.yml --limit labgrid-switchlab --tags export`). Without
   them the module skips.

## Next steps

1. Fix the USW and LGS serial wiring. Then USW: fix the U-Boot prompt and
   load address from its console; LGS: check the `acp` interrupt and the
   U-Boot prompt. Drop each from `maintenance:` in `labs/labgrid-switchlab/lab.yaml` once it
   passes its health check, and deploy (`lab.yml --limit labgrid-switchlab
   --tags export`).
