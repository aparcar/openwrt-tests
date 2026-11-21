# Adding a New Device

A device in a lab is made of four pieces:

| File | Contains | Shared? |
| --- | --- | --- |
| `targets/<device>.yaml` | labgrid drivers, prompts, strategy, `openwrt:` metadata | yes, by every lab with this model |
| `labs/<lab>/exporter.yaml` | serial port, power outlet, network, TFTP/SD-Mux of this one board | no, per lab |
| `labs/<lab>/lab.yaml` | which devices a lab offers to CI | per lab |
| `docs/labs/<lab>.md` | human-readable wiring table | per lab |

`<device>` is `<vendor>_<openwrt profile>` (e.g. `openwrt_one`,
`starfive_visionfive2-v1.2a`). If the model already has a target file, skip to
[the exporter](#3-exporter-entry). If you have several boards of the same
model, see [sharing-target-files.md](sharing-target-files.md).

## 1. Find out how the board boots

Answer these before writing anything:

1. **Which images does OpenWrt publish for it?** Check `profiles.json` of the
   target, e.g.

   ```shell
   curl -s https://downloads.openwrt.org/snapshots/targets/starfive/generic/profiles.json \
     | jq '.profiles["visionfive2-v1.2a"].images[] | {type, filesystem, name}'
   ```

   CI and the healthcheck download firmware from these lists, so the image the
   device boots **must** be one of them. An `initramfs` image (`type: kernel`)
   is the easiest to netboot; `sdcard`/`factory`/`sysupgrade` images need an
   SD-Mux or flashing.
2. **How does the firmware get onto the board?** This picks the strategy:

   | Boot flow | Strategy | Needs |
   | --- | --- | --- |
   | U-Boot loads an initramfs over TFTP | `UBootTFTPStrategy` (`strategies/tftpstrategy.py`) | `TFTPProvider`, interruptible U-Boot |
   | Image is written to an SD card | `SDMuxStrategy` (`strategies/sdmuxstrategy.py`) | USB-SD-Mux, image with a bootloader on it |
   | Broadcom CFE flashes the TRX over TFTP (bcm47xx, no initramfs published) | `CFETFTPStrategy` (`strategies/cfestrategy.py`) | `TFTPProvider`, CFE interruptible with Ctrl+C, CFE able to ping the TFTP server |
   | U-Boot loads a flashable image over TFTP, writes it to flash and boots it (no initramfs boot possible) | `UBootTFTPStrategy` with `flash_commands` (see `targets/arcadyan_vgv7510kw22-nor.yaml`) | `TFTPProvider`, interruptible U-Boot, an image that is the raw content of the firmware partition |
   | QEMU | `QEMUNetworkStrategy` | nothing on the lab side |

   Prefer TFTP: it never wears out flash and survives a broken image.
3. **What does the U-Boot prompt look like, and what commands boot an
   initramfs by hand?** Get this working on the serial console first; the
   target file only automates it.

## 2. Target file

Copy the target of a board with the same boot flow and adjust it. A TFTP
example, trimmed from `targets/starfive_visionfive2-v1.2a.yaml`:

```yaml
openwrt:
  name: StarFive VisionFive2 v1.2a
  target: starfive-generic
  profile: visionfive2-v1.2a
targets:
  main:
    features:
      - wan_port
    resources:
      RemotePlace:
        name: !template "$LG_PLACE"
    drivers:
      - PDUDaemonDriver: {}
      - TFTPProviderDriver: {}
      - SerialDriver:
          txdelay: 0.01
      - UBootDriver:
          prompt: "StarFive #"
          init_commands:
            - setenv autoload no
            - dhcp
          boot_command: tftpboot 0x60000000 $bootfile; bootm 0x60000000
      - ShellDriver:
          prompt: 'root@[\w()]+:[^ ]+ '
          login_prompt: Please press Enter to activate this console.
          await_login_timeout: 15
          login_timeout: 120
          post_login_settle_time: 5
          username: root
      - UBootTFTPStrategy: {}
      - SSHDriver:
          connection_timeout: 120.0
          explicit_scp_mode: True
images:
  root: !template $LG_IMAGE
imports:
  - ../strategies/tftpstrategy.py
```

- `openwrt:` is read by the healthcheck to pick the firmware;
  set `image.type`/`image.filesystem` if the image isn't `type: kernel`. See
  [openwrt-target-attributes.md](openwrt-target-attributes.md).
- `features:` declares what the board can do (`wan_port`, `rootfs`,
  `wifi-2g`/`wifi-5g`/`wifi-6g`); tests needing a feature are skipped
  otherwise. Only list what is actually wired up.
- `UBootTFTPStrategy` stages `LG_IMAGE` under a hashed name and runs
  `setenv bootfile <name>` before your `init_commands`, so use `$bootfile`.
- Never hardcode the board's IP, serial port or outlet here: that belongs in
  the exporter.

## 3. Exporter entry

Plug the board in, then run `labgrid-suggest` on the lab host. It prints a
`match:` block for every USB device that appears (serial adapter, SD-Mux,
USB power port). Prefer the `ID_PATH` match: it names the USB port, so a
replaced adapter keeps working.

Add a place named `<lab>-<device>` to `labs/<lab>/exporter.yaml`:

```yaml
labgrid-aparcar-starfive_visionfive2-v1.2a:
  NetworkService:
    address: 192.168.1.1%vlan119   # board's LAN address, via its lab VLAN
    username: root
  PDUDaemonPort:
    host: localhost:16421
    index: 19                      # PDU outlet / PoE port
    pdu: 192.168.128.2
  TFTPProvider:
    external: tftp://192.168.119.1/starfive_visionfive2-v1.2a/
    internal: /srv/tftp/starfive_visionfive2-v1.2a/
  USBSerialPort:
    match:
      ID_PATH: platform-xhci-hcd.1-usb-0:2.1.2:1.0
```

- If the board's WAN port is cabled to the lab switch, add a `wan` resource
  naming the lab-host VLAN it ends up on:

  ```yaml
    wan:
      cls: NetworkInterface
      ifname: vlan118                ## the VLAN of the DUT's WAN switch port
  ```

  Tests read it as the place's `RemoteNetworkInterface` named `wan`.
  `tests/test_probe_lan.py` uses it for the WAN side of its speedtest, and
  skips WAN tests on places without one. The target's `wan_port` feature only
  says the board *has* a WAN port; this resource says it's *cabled* in this lab.
  The place's `*/<group>/*` match picks it up automatically.
- `USBSerialPort` defaults to 115200 baud; add `speed:` if the board differs.
  Multi-port adapters also need `'@port_number'`.
- SD-Mux boards get a `USBSDMuxDevice` (`match: ID_PATH: …`) instead of a
  `TFTPProvider`.
- `TFTPProvider.external` is always a full `tftp://<server>/<path>/` URL,
  where `<server>` is the lab host's address on the board's VLAN
  (`192.168.<vlan>.1`). The strategy sets U-Boot's `serverip` to it and
  `ipaddr` to the next address (`.2`), so U-Boot never needs DHCP. In the
  target's `UBootDriver`, load the image with `tftpboot` (it uses `$bootfile`
  and `$loadaddr`), not `dhcp`. The DUT's LAN segment then has only one DHCP
  server: the DUT itself, once it has booted.

## 4. Register the device

Add the device to the lab's `devices:` list in `labs/<lab>/lab.yaml`. This is what makes
CI and the healthcheck pick it up, so do it once the device boots. Add a row to
`docs/labs/<lab>.md` as well.

Then deploy the exporter config (this also creates `/srv/tftp/<device>/`):

```shell
cd ansible
uv run ansible-playbook -i inventory.yaml lab.yml --limit labgrid-aparcar --tags export
```

## 5. Test it

Write a local env file (`env/` and `firmware/` are git-ignored), e.g.
`env/labgrid-aparcar-starfive_visionfive2-v1.2a.env`:

```shell
export LG_ENV=targets/starfive_visionfive2-v1.2a.yaml
export LG_PLACE=labgrid-aparcar-starfive_visionfive2-v1.2a
export LG_PROXY=labgrid-aparcar
export LG_IMAGE=../firmware/openwrt-starfive-generic-visionfive2-v1.2a-initramfs-initramfs.itb
```

Then lock the place and run the tests:

```shell
source env/labgrid-aparcar-starfive_visionfive2-v1.2a.env
uv run labgrid-client lock
uv run pytest tests/ --lg-log --lg-colored-steps --log-cli-level=CONSOLE
uv run labgrid-client unlock
```

Finally run the CI healthcheck, which downloads the firmware from the
`openwrt:` metadata just like CI will:

```shell
./scripts/healthcheck.sh labgrid-aparcar starfive_visionfive2-v1.2a
```

## Troubleshooting

Use `uv run labgrid-client console` and `uv run labgrid-client power cycle` to
watch and drive the boot by hand.

- **`USBStorageDriver.write_image()`: Timeout while waiting for medium** — the
  SD-Mux sees no card in host mode. On the lab host, `lsblk` shows the mux's
  disk with size 0. Check the card and re-plug the mux.
- **SD image doesn't boot** — not every OpenWrt SD image carries a bootloader.
  The StarFive images, for example, have empty SPL/U-Boot partitions and rely
  on the SPI flash; check the partitions before blaming the mux.
- **U-Boot can't see the SD card at all** (`Card did not respond to voltage
  select`) — test with the card directly in the board. If that fails too,
  netboot instead.
- **`bootm` fails with overlapping images** — load the FIT above the kernel's
  load address (`mkimage -l <image>` shows it), not at `$loadaddr`.
- **Silent hang after `Starting kernel ...`** — often the device tree. Linux
  needs it 8-byte aligned; if U-Boot says `Using Device Tree in place` at an
  odd address, make it relocate the dtb with `setenv fdt_high <limit>`. Don't
  just unset `fdt_high`: U-Boot may then copy it over itself at the top of RAM
  and hang on `Loading Device Tree to …`.
- **Garbled console** — wrong baud rate; set `speed:` on the `USBSerialPort`.
