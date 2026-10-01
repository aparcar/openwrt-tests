# OpenWrt lab image

Instead of provisioning a Debian host with Ansible (`ansible/lab.yml`), a lab
host can run OpenWrt with its own labgrid coordinator and exporter. The
image is generated from `labnet.yaml` and `ansible/files/exporter/<lab>/`.
Provisioning a lab means flashing the image. Updating it means a sysupgrade.

```shell
make lab-image LAB=labgrid-aparcar PLATFORM=rpi-5
# or
make -C lab-image LAB=labgrid-aparcar PLATFORM=x86-64 VERSION=24.10.2
```

Images end up in `lab-image/bin/<lab>-<platform>/`.

| Variable          | Default                  | Description                                          |
| ----------------- | ------------------------ | ---------------------------------------------------- |
| `LAB`             | (required)               | Lab name from `labnet.yaml`                           |
| `PLATFORM`        | `rpi-4`                  | `x86-64`, `rpi-3`, `rpi-4` or `rpi-5`                 |
| `VERSION`         | current stable           | OpenWrt release or `snapshot`                         |
| `PYTHON_VERSION`  | detected from the feed   | Python version of OpenWrt's `python3` package         |
| `LABGRID_SRC`     | `aparcar/staging` branch | pip requirement used to build labgrid                 |
| `TRUNK` / `WAN`   | `eth0`                   | Interface with the DUT VLANs / uplink using DHCP      |
| `FILES_EXTRA`     |                          | Directory copied on top of the image files            |
| `EXTRA_PACKAGES`  |                          | Additional OpenWrt packages                           |
| `OFFLINE`         |                          | Set to `1` to skip fetching SSH keys from GitHub      |

The build needs `uv`, `curl`, `git` and the
[ImageBuilder dependencies](https://openwrt.org/docs/guide-user/additional-software/imagebuilder#prerequisites).

`TRUNK` and `WAN` can also be stored per lab in `labnet.yaml`:

```yaml
labs:
  labgrid-example:
    image:
      trunk: eth1
      wan: eth0
```

## How it works

- **labgrid**: built from git, with all of its dependencies downloaded as
  prebuilt `musllinux` wheels for the platform. Everything goes into
  `/usr/lib/labgrid`, so no pip runs on the device. Wrappers live in
  `/usr/bin/labgrid-{coordinator,exporter,client}`. gRPC isn't packaged for
  OpenWrt, which is why only aarch64 and x86_64 are supported.
- **Coordinator**: procd service running as `labgrid-dev` and listening on
  `127.0.0.1:20408`. Clients reach it through `LG_PROXY`, as before.
  `places.yaml` is generated from `labnet.yaml`.
- **Exporter**: procd service running as root, using
  `/etc/labgrid/exporter.yaml`.
- **Serial ports**: OpenWrt has no udevd, so `USBSerialPort` can't match on
  `ID_PATH`. The generator rewrites it to a `RawSerialPort` using links
  created by `/etc/hotplug.d/tty/50-labgrid-serial`:
  - `ID_PATH` (+ `@port_number`) → `/dev/labgrid/by-path/<ID_PATH>-port<N>`
  - `ID_SERIAL_SHORT` (+ `ID_USB_INTERFACE_NUM`) →
    `/dev/labgrid/by-serial/<serial>-if<NN>-port<N>`

  The script computes `ID_PATH` the same way udev's `path_id` does. It also
  creates udev-style `/dev/serial/by-id` and `/dev/serial/by-path` links.
  Check `ls -l /dev/labgrid/*/` on the device if a port doesn't show up.
- **Power**: pdudaemon is replaced by a small CGI script
  (`/www-pdu/power/control`) that uhttpd serves on `localhost:16421`. It
  speaks pdudaemon's HTTP API and reads the same `pdudaemon.conf`. It
  supports the `ubus`, `tasmota`, `netio4` and `localcmdline` drivers.
- **Network**: DHCP on the uplink. 802.1q VLANs `vlan101`–`vlan124` and
  `vlan200` on the trunk, plus any `%vlanNNN` used in `exporter.yaml`. Each
  VLAN gets `192.168.N.1/24` and `192.168.1.N/24`. dnsmasq serves DHCP and
  TFTP from `/srv/tftp`. The VLANs form a firewall zone that may forward to
  the uplink. SSH is allowed from the uplink.
- **Access**: `labgrid-dev` gets the GitHub keys of everyone in
  `maintainers` and `access`, plus the CI key. Maintainers also get root.
  `labgrid-bound-connect` is a shell script permitted via sudo.
- `procd-ujail` is left out because jailed dnsmasq can't follow the TFTP
  symlinks into `/var/cache/labgrid`.

## Migrating a lab

- **SSH host key**: CI pins it via `hostkey` in `labnet.yaml`. Either update
  that entry after the first boot, or keep the old key by placing it in
  `FILES_EXTRA` as `etc/ssh/ssh_host_ed25519_key` (+ `.pub`). Images built
  with secrets in `FILES_EXTRA` must not be published.
- **WireGuard**: `wireguard-tools` is included. Configure it with a
  `FILES_EXTRA/etc/uci-defaults/95-wireguard` script.
- **Not supported**: udev based resources (`USBSDMuxDevice`, `USBPowerPort`)
  are kept in the config but won't be found. Custom `netplan.yaml` and
  `dnsmasq.conf` files aren't converted; only their VLANs carry over.
  Interfaces like `eth0.201` need a custom uci-defaults script. The
  generator prints warnings for all of these.
- Scripts used by `localcmdline` PDUs (for example fcefyn's relay scripts)
  must be added via `FILES_EXTRA`.
- `/var` is a tmpfs, so files staged by labgrid live in RAM and are gone
  after a reboot.
