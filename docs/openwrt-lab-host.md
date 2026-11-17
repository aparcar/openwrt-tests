# OpenWrt as a lab host

A lab host can run OpenWrt instead of Debian/Ubuntu. Role
`labgrid_lab_openwrt` (in place of `labgrid_lab`) sets it up the same way:
labgrid coordinator and exporter, the `labgrid-dev` developer account, TFTP.
Role `labgrid_agent` runs the kernelci agent on it as on any other lab host.
`lab.yml` picks the role by `ansible_os_family`, so nothing else changes:
add the host to `labnet.yaml` and the inventory as usual.

## Requirements

- **aarch64 or x86_64.** labgrid needs `grpcio`, and uv's Python and the
  wheels exist for musl on these two only (armv7 and MIPS would have to build
  grpcio on the device).
- **Persistent storage** of a few GB (eMMC, NVMe or extroot): uv, its Python,
  labgrid and the agent's two venvs take several hundred MB, plus firmware
  and job artifacts. `/var` is a tmpfs on OpenWrt, so the agent keeps its
  state in `/srv/labgrid-agent`.
- **1 GB RAM** or more is comfortable (pytest, the exporter, ser2net, and
  labgrid's staging cache in `/var/cache/labgrid`).
- OpenWrt 24.10 (opkg) or 25.x (apk), reachable as **root with an SSH key**.
  The role turns off password logins in dropbear once root has a key.

## Extroot on flash-only hosts

Flash alone isn't enough: the OpenWrt One's UBI leaves an overlay of about
80 MB, and uv's Python alone unpacks to over 100 MB. Building the packages
into the firmware doesn't help much, as the image and the overlay share the
same flash. Attach a USB drive or NVMe and move the overlay onto it (extroot)
before running `lab.yml`:

```sh
apk update
apk add block-mount kmod-fs-ext4 e2fsprogs kmod-usb-storage   # or kmod-nvme
mkfs.ext4 -L extroot /dev/sda     # erases the drive

eval $(block info /dev/sda | grep -o -e 'UUID="\S*"')
eval $(block info | grep -o -e 'MOUNT="\S*/overlay"')
uci -q delete fstab.extroot
uci set fstab.extroot=mount
uci set fstab.extroot.uuid="$UUID"
uci set fstab.extroot.target="$MOUNT"
uci commit fstab

mount /dev/sda /mnt
tar -C "$MOUNT" -cf - . | tar -C /mnt -xf -
umount /mnt
reboot
```

Afterwards `df -h /` shows the drive's size. Without the drive, OpenWrt
boots with the flash overlay.

## Inventory

```yaml
labgrid-<lab>:
  ansible_host: <address>
  ansible_user: root
  ansible_pipelining: true
```

`lab.yml` first installs `python3` and `sudo` with a raw task (Ansible needs
both), then gathers facts.

## What the role does

- Packages: ser2net, socat, OpenSSH client and sftp-server (labgrid's SSH and
  scp; dropbear stays the server), shadow tools for the accounts, and more
  (`labgrid_openwrt_packages`).
- uv's musl build in `/usr/bin` (dropbear's non-interactive `PATH` has no
  `/usr/local/bin`), labgrid as a uv tool with uv's own Python, and
  `/usr/bin/labgrid-python3` for labgrid's remote helpers (GPIO).
- procd services `labgrid-coordinator` (loopback only; developers come in
  through `LG_PROXY`), `labgrid-exporter` and `labgrid-agent` (long-running,
  like the systemd service elsewhere; `/etc/init.d/labgrid-agent reload`
  drains it: running jobs finish, then it starts again). Their output goes to
  the system log (`logread`).
- `/etc/init.d/labgrid-lab` at boot: without udev, it gives group `labgrid`
  the `RawSerialPort` ports and `SysfsGPIO` lines of the lab's
  `exporter.yaml`, exporting the GPIOs (it never changes their value).
- procd service `labgrid-tftp`: a dnsmasq of its own serves `/srv/tftp`
  over TFTP only, on the server addresses of the `tftp://` URLs in
  `exporter.yaml`. The system dnsmasq runs in a ujail that only mounts its
  `tftp_root`, so labgrid's symlinks (to its staging cache or the agent's
  firmware cache) would dangle there; the role turns its TFTP off.

The host can also be the lab's labgrid-probe (`install-probe.sh local`, not
Ansible): its slots are macvlans on the host's DUT interfaces, so one
OpenWrt box is lab host and probe. See "On the lab host" in
`contrib/labgrid-probe/README.md`.

Not supported: `USBSerialPort` matching (no udev, use `RawSerialPort` with the
device node), pdudaemon and usbsdmux, QEMU platforms.

## DUT network

Set it in the host's `labgrid_openwrt_uci` (host_vars; example with one
VLAN per DUT: `ansible/host_vars/labgrid-blocktrron.yml`). The role replaces
the sections listed there as a whole and leaves all others, like wan,
alone; `--tags network` applies only this. Per
DUT, give its port (or VLAN) two addresses: `192.168.10<k>.1` as the TFTP
server for U-Boot (the strategy sets `serverip`, and `ipaddr` = `.2`), and
`192.168.1.<k+1>` to reach the booted OpenWrt at 192.168.1.1, for DUT
number k = 1, 2, …. Keep the second one below .100: the DUT's DHCP range is
.100–.249, and a probe slot on that port (labgrid-probe, local mode) gets a
lease from it, which must never be the host's own address. The host's own
LAN must not be 192.168.1.0/24, and the DUT port must not be part of it:
the host must serve no DHCP and send no RAs there (the DUT is the router on
that segment, and a probe slot sees the host's RAs, too).

```yaml
labgrid_openwrt_uci:
  network:
    interface lan:                # the host's own LAN, moved off 192.168.1.0/24
      device: br-lan
      proto: static
      ipaddr: [192.168.42.1/24]
    interface dut1:
      proto: static
      device: eth1
      ipaddr: [192.168.101.1/24, 192.168.1.2/24]   # below the DUT's DHCP range
  firewall:
    zone dut:                     # TFTP and SSH from/to the DUT, nothing forwarded
      name: dut
      network: [dut1]
      input: ACCEPT
      output: ACCEPT
      forward: REJECT
```

(Here eth1 must also leave the stock br-lan bridge's `ports`, an anonymous
section this can't replace: remove it with `uci` once.)

`exporter.yaml` then uses `external: "tftp://192.168.101.1/<device>/"` and
`NetworkService: address: 192.168.1.1%eth1` (the Linux device, not the UCI
interface name).

## GPIO numbers

`SysfsGPIO` wants the global sysfs number, the chip's base plus the line:
`cat /sys/class/gpio/gpiochip*/base` and `.../label`. With a base of 512,
line 22 is 534.

## Sysupgrade

A sysupgrade drops the installed packages and everything outside the kept
config. Run `lab.yml` again afterwards; with the
storage on extroot, restore the extroot mount first.
