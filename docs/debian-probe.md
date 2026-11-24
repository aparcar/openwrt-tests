# Debian lab probe (dprobe)

A small x86 (or arm64) box running Debian that sits on the DUTs' LAN and WAN
VLANs, like the OpenWrt [labgrid-probe](../contrib/labgrid-probe/README.md),
but with tools OpenWrt doesn't have: scapy, kea-dhcp6 with prefix
delegation, radvd, dnsmasq with arbitrary DHCP options, chrony, and later
pppoe-server, a DS-Lite AFTR, multicast sources and a Wi-Fi station.

- On the probe: `contrib/debian-probe/dprobe/`, a self-contained Python
  package with the `dprobe` CLI. It is not imported by the tests.
- In openwrt-tests: `drivers/debianprobedriver.py` runs `dprobe` over SSH,
  and `tests/debian_probe_fixtures.py` provides the fixtures.
- Tests: `tests/test_dprobe_wan.py` and `tests/test_dprobe_lan.py`.

```
lab host ──SSH (LG_PROXY)──► probe trunk: mgmt untagged, DUT VLANs tagged
                               ├─ mgmt: sshd, sudo dprobe
                               ├─ netns dp-lan<N>: dpl<N> = VLAN N (DUT LAN)
                               │     scapy DHCP clients, RA capture, dns-pair
                               │          ▼
                               │     DUT LAN 192.168.1.1 → DUT → DUT WAN
                               │          ▼
                               └─ netns dp-wan<N>: dpw<N> = DUT WAN VLAN
                                     172.16.100.1/24, 2001:db8:100::1/64
                                     dnsmasq, kea-dhcp6, radvd, chrony
```

## Slots

A slot is one DUT's pair of namespaces, keyed by the DUT's LAN VLAN `N`.
Nothing is configured per DUT on the probe: the fixture passes the VLANs,
which come from the DUT's place (`NetworkService` `192.168.1.1%vlan<N>`
and the `wan` `NetworkInterface`, as for the OpenWrt probe).

Every slot uses the same WAN addresses (`172.16.100.0/24`,
`2001:db8:100::/64`, PD pool `2001:db8:200::/48` handed out as /60s), so
tests need no per-DUT constants.

**Namespaces instead of VRFs.** A VRF per side would also work. That
model fits slots on the lab host, which must keep reaching the DUTs from
its main namespace (exporter, TFTP). The probe is a separate box with no
such constraint, so each side gets its own network namespace. A namespace
has its own conntrack table, so conntrack zones aren't needed, and nothing
needs VRF awareness. Daemons run as transient systemd units
(`dprobe-<N>-<service>.service`) under `ip netns exec`. `systemctl` and
`journalctl` show them.

**Sharing.** The probe place is *not* locked. Each DUT only touches its
own slot, and the DUT's place lock already makes that slot exclusive, so
several DUTs can use one probe at the same time. The `dprobe` fixture reads
the probe's `NetworkService` from the coordinator without acquiring the
place.

## The `dprobe` CLI

Each command prints one JSON document and exits with one of these codes:

- `0`: ok.
- `1`: it ran, but the expected thing didn't happen (no lease, no RA,
  unanswered DNS). The reply has `error` plus everything collected.
- `2`: error (bad arguments, slot not up, a command failed). The reply is
  `{"error": ...}`, and `not_up: true` means the slot wasn't brought up.

| Command | What it does |
|---|---|
| `slot up N [--wan-vlan W] [--trunk IF]` | (Re)create the slot, return names, MACs, addresses |
| `slot down N` / `slot info N` / `slot list` | Remove, inspect, list |
| `wan dhcp4 N [--option CODE=VALUE]... [--range A,B] [--lease-time T] [--dns --host NAME=ADDR]` | dnsmasq DHCPv4 on the WAN. `--option` is forced into every reply |
| `wan dhcp6 N [--no-na] [--no-pd] [--pd-len L] [--preferred S --valid S --t1 S --t2 S] [--rapid-commit] [--keep-leases]` | kea-dhcp6 with IA_NA/IA_PD, starting with no bindings by default |
| `wan ra N [--no-managed] [--no-other] [--no-autonomous] [--router-lifetime S] [--no-rdnss]` | radvd for `2001:db8:100::/64` |
| `wan ntp N` | chronyd serving the probe's clock on `172.16.100.1` (never steers it) |
| `wan stop N dhcp4\|dhcp6\|ra\|ntp\|all` / `wan leases N` | Stop a server, or list the current leases (dnsmasq and kea) |
| `logs N SERVICE` | Journal of a slot service |
| `addresses N [--side lan\|wan]` | Addresses on the slot device |
| `ping N HOST [--side]` | ping from the namespace (exit 1 if nothing comes back) |
| `ra-capture N [--side] [-d S] [-c COUNT] [--no-solicit]` | Send an RS, then decode RAs (flags, lifetimes, PIO, RIO, RDNSS, DNSSL, MTU) |
| `dhcp4 N [--side] [--mac M] [--server IP] [--params 1,3,28] [--apply]` | scapy DISCOVER/REQUEST with all options decoded. `--apply` configures the lease in the namespace |
| `dhcp6 N [--side] [--mac M] [--no-na] [--no-pd] [--pd-len L] [--no-request]` | SOLICIT/REQUEST, decoded ADVERTISE/REPLY |
| `dns-pair N SERVER NAME [--tries T]` | A+AAAA back to back on one socket per try (#16261) |

On the probe, for example: `sudo dprobe slot up 102 --wan-vlan 302`, then
`sudo dprobe wan dhcp6 102 --preferred 60 --valid 120`.

## In tests

The `conftest.py` fixtures are:

- `dprobe` (module): the `DebianProbeDriver`. It is skipped without a
  coordinator (QEMU) or without a `role=debian-probe` place.
- `dprobe_slot` (module): boots the DUT, then brings up its slot (and the
  WAN side if the place has a `wan` interface), and removes it afterwards.
- `dprobe_wan`: the same slot, but skipped without a WAN cable.
- `dprobe_lan_lease`: a DHCPv4 lease from the DUT, configured in the LAN
  namespace (needed for dns-pair and pings).

`slot.call("wan", "dhcp6", preferred=60)` runs `dprobe wan dhcp6 <N>
--preferred 60`. Keyword arguments become flags: `True` gives `--flag`,
`False` gives `--no-flag`, and a list repeats the flag. `call()` returns a
dict with `.ok` (False on exit 1). Exit 2 raises `DebianProbeError`, and so
does `check()` on exit 1.

`debian_probe_fixtures.py` also has `dut_config()` (back up `/etc/config/*`
and restore it), `uci()`, `wait_for_interface()` and `record_json()`.

| Test | Issue |
|---|---|
| `test_wan_dhcp_broadcast_option` | #24782: option 28 applied to the WAN address |
| `test_wan6_metric_applied` | #10188: wan6 `metric` on odhcp6c routes |
| `test_wan6_pd_survives_reconfigure` | #13454: PD expiry and server restart, wan6 re-acquires (the Reconfigure trigger is not covered: kea needs a commercial hook) |
| `test_odhcpd_ra_with_device_default_route` | #23246: nexthop-less default route, RA lifetime > 0 after an odhcpd restart |
| `test_lan_dns_udp_pair` | #16261 (and #18122, #19478, #23542): back-to-back A+AAAA all answered |

## Setup

**Hardware:** any amd64/arm64 box with one Ethernet port (two are useful:
trunk plus a separate admin port), 2 GB RAM and 8 GB of storage. A
Wi-Fi card (e.g. Intel AX210) comes later for the station tests.

**OS:** Debian 13 "trixie" or newer. dprobe needs Python 3.12 or newer for
`os.setns`, plus systemd and AppArmor as Debian ships them. kea and chrony
keep their files in `/etc/kea` + `/var/lib/kea` and `/etc/chrony` +
`/run/chrony`, where their AppArmor profiles allow access.

**Provisioning:** `contrib/debian-probe/ansible/`:

```shell
cd contrib/debian-probe/ansible
cp inventory.example.yml inventory.yml   # trunk, allowed VLANs, SSH keys
ansible-playbook -i inventory.yml probe.yml
```

Alternatively, `contrib/debian-probe/cloud-init.yaml` provisions a fresh
Debian cloud image (or a NoCloud seed) without a control host: cloud-init
installs Ansible and runs the same playbook locally with `ansible-pull`.
Fill in the inventory in its `write_files` first. To update the probe
later, run the `ansible-pull` command from its `runcmd` again as root.

The role does the following:
- installs the packages and masks the package units of kea, radvd and
  iperf3;
- loads `8021q`;
- copies `dprobe/` to `/opt/dprobe` with the wrapper
  `/usr/local/sbin/dprobe` (`python3 -I`);
- writes `/etc/dprobe/dprobe.json` (`trunk`, `mgmt_vlan`, `allowed_vlans`);
- creates the `dprobe` user with a sudoers rule for `/usr/local/sbin/dprobe`
  only. It may log in with the GitHub keys of `labgrid_access` (as for lab
  hosts; on a lab host its `host_vars` already have it), with
  `dprobe_authorized_keys` and, on a lab host, with the local `labgrid-agent`'s
  key, each in its own Ansible-managed block of `authorized_keys`. An SSH login as
  `dprobe` runs nothing but `sudo -n /usr/local/sbin/dprobe <args>` (and
  labgrid's keepalive `cat`): sshd forces `probe-ssh`
  (`contrib/probe-ssh/probe_ssh.py`) on it, as for the phone probes, so there
  is no shell, TTY or forwarding;
- keeps networkd away from `dpl*`/`dpw*` and, optionally, writes the
  trunk's management config.

**Switch:** the probe's trunk port carries management untagged and every
DUT LAN and WAN VLAN tagged, as for the OpenWrt probe.

**labgrid:** the probe is a place of its own, tagged `role=debian-probe`,
with an SSH `NetworkService`:

```yaml
# labs/<lab>/exporter.yaml
labgrid-<lab>-debian-probe:
  NetworkService:
    address: 192.168.128.4
    username: dprobe
```

```yaml
# places.yaml (labgrid_lab role), next to the -probe place
labgrid-<lab>-debian-probe:
  matches: [{exporter: '*', group: labgrid-<lab>-debian-probe, cls: '*'}]
  tags: {role: debian-probe}
```

Whoever runs the tests must be able to log in: developers through
`labgrid_access`, the lab's `labgrid-agent` through `dprobe_authorized_keys`
(or automatically when dprobe runs on the lab host).

## Unit tests

```shell
tests/.venv/bin/pytest tests/contrib/debian-probe/tests
```

These cover the config renderers, lease parsers, the RA/DNS/DHCP decoders
(the DHCP ones only where scapy is installed), the CLI parser, and the
driver's argument and exit-code mapping against that parser.

## Licence

LGPL-2.1, like the rest of this repository.
