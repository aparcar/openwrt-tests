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
| Linksys LGS328MPC v2 | 4 | ttyUSB0 (1.2) | lan2 (copper) | 102 | U-Boot 2011.12.(3.6.0.48) (Realtek SDK), 115200, `Enter correct key to stop autoboot` (3 s, `acp`) |
| XikeStor SKS8300-12X V1 | 2 | ttyUSB1 (1.3) | lan9 (10G SFP+; SKS port 1, FLEXOPTIX 10GBase-R) | 109 | vendor "U-boot version: V2.00", **9600**, `Hit Ctrl+B to stop autoboot` (1 s); OpenWrt at 115200 |
| Ubiquiti USW Aggregation | 3 | ttyUSB2 (1.4) | lan10 (10G SFP+, LR fibre to USW lan1) | 110 | U-Boot usw-US_v1.0.25.77, 115200, `Hit Esc key to stop autoboot` (1 s), prompt `uboot> # ` |

All three have OpenWrt (snapshot) in flash and answer on 192.168.1.1 through
their VLAN. Outlet 1 is unused.

## Status (2026-11-25)

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
- **USW Aggregation**: works since its console was fixed. `test_base.py`
  passes from a local run (snapshot r38552, boot about 80 s). U-Boot
  (RTL9300, 256 MB, 16 MB SPI-NOR) stops at one Esc and prompts
  `uboot> # `; it has no `echo`, so the target uses `SmallUBootDriver`.
  Its network is off until `rtk network on` (hardware profile `UBNT_USL8A`,
  about 4 s); TFTP loads to `0x82000000` over the fibre in lan1, the
  legacy uImage runs at `0x80100000`. Since 2026-11-26 that TFTP times out
  on every boot (U-Boot doesn't bring the SFP+ module up), so the target
  uses `FlashBootStrategy` instead: the OpenWrt in flash copies the image
  under test over SSH, writes it to `firmware` (kernel0, where U-Boot boots
  from) and the board is power cycled. Recovery if an image in flash
  doesn't boot: the U-Boot TFTP steps above, by hand.
- **LGS328MPC**: the console now shows U-Boot (`U-Boot
  2011.12.(3.6.0.48)`, RTL9300, 512 MB, 128 MB SPI-NAND; the flash boot
  loads to `0x81000000`), but with noise bursts while U-Boot and Linux set
  up the switch hardware, and corrupted characters. `acp` sent at the
  autoboot line never stopped it, so input probably arrives garbled too.
  Check the console cable and ground of ttyUSB0 (hub port 1.2), with the
  USW powered off. Stays in `maintenance:`; the U-Boot prompt is unknown.

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

1. Fix the LGS serial noise. Then check the `acp` interrupt (likely needs
   repeating during the 3 s countdown) and the U-Boot prompt, and boot the
   initramfs from `0x81000000`. Drop it from `maintenance:` in
   `labs/labgrid-switchlab/lab.yaml` once it passes its health check, and
   deploy (`lab.yml --limit labgrid-switchlab --tags export`).
2. The lab host's sshd resets new connections when a client opens many in a
   short time (`kex_exchange_identification: read: Connection reset by
   peer`); check `MaxStartups`/`PerSourcePenalties` before CI runs here.
