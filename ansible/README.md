# Lab host provisioning

`lab.yml` configures a lab host with these roles:

| Role | Hosts | Sets up |
|---|---|---|
| `roles/labgrid_lab` | Debian lab hosts | accounts, labgrid exporter + coordinator, pdudaemon, TFTP, DUT VLANs (netplan, dnsmasq), udev, serial drivers, unattended-upgrades |
| `roles/labgrid_lab_openwrt` | lab hosts running OpenWrt (aarch64/x86_64), in place of `labgrid_lab` | the same with procd, apk/opkg and UCI; no pdudaemon, usbsdmux, VLANs or automatic upgrades ([docs/openwrt-lab-host.md](../docs/openwrt-lab-host.md)) |
| `roles/labgrid_agent` | hosts with `labgrid_agent_lab_name` | kernelci-labgrid's `labgrid-agent`, polling the CI server and running jobs in the background (systemd service, or a procd service on OpenWrt; `reload` drains: finishes the running jobs, then restarts) |

```shell
uv run ansible-playbook lab.yml --limit labgrid-<lab>                 # whole lab host
uv run ansible-playbook lab.yml --limit labgrid-<lab> --tags export   # exporter.yaml, places, TFTP dirs
uv run ansible-playbook lab.yml --limit labgrid-<lab> --tags accounts # accounts, groups, shared directories
uv run ansible-playbook lab.yml --limit labgrid-<lab> --tags agent    # kernelci agent only
uv run ansible-playbook reset-health.yml                              # fresh health checks in all labs
```

Connection settings and passwords go into `host_vars/<host>.yml`
(gitignored), everything else into `inventory.yaml`.

`labgrid_lab` needs **Debian 13 (trixie)**, also as Raspberry Pi OS (64-bit),
and stops on other releases (`labgrid_lab_debian_version`). It also checks
ser2net is at least 4.6.1 (`labgrid_lab_ser2net_min_version`): older ones
mishandle RFC 2217 purge requests, so labgrid sometimes can't open a serial
console (`timeout while waiting for option 'purge'`, labgrid issue #1396).

## The kernelci agent

The CI server (openwrtci) issues each lab's tokens: add the lab to
`openwrtci_labs` there and run its `server.yml --tags labs`, which writes
`ansible/secrets/<server>/labs/<lab>.env`. Here, set the host's
`labgrid_agent_lab_name` to that lab name; `labgrid_agent_secrets_dir`
(`inventory.yaml`) points to openwrtci's secrets directory, by default
relative to this checkout inside openwrtci (`openwrtci/tests`). Optional per
host: `labgrid_agent_platforms` (the `qemu_*` platforms of QEMU labs),
`labgrid_agent_health_checks` (`false`, or a mapping of the agent's
`[health_checks]` settings, e.g. `{rpi-4: {enabled: false}}`) and
`labgrid_agent_max_jobs`. See `roles/labgrid_agent/defaults/main.yml`.

Automatic upgrades (role `labgrid_lab`) reboot the host at
`labgrid_lab_upgrades_reboot_time` when an upgrade needs it; agent jobs
running then go back to the queue. Set `labgrid_lab_upgrades: false` for a
host whose admin manages updates.

## Accounts

| Account | Kind | Purpose | Access |
|---|---|---|---|
| `labgrid-dev` | login | shared developer account for running tests | SSH keys: the GitHub keys (`github.com/<user>.keys`) of the lab's `maintainers` and `access` in `labs/<lab>/lab.yaml`, in an Ansible-managed block of `authorized_keys`; keys outside the block (added by hand) stay. Groups: `labgrid`, `dialout`, `plugdev`. Sudo only for `labgrid-bound-connect`. Home `0750`. |
| `labgrid-agent` | system, login via its own key | runs kernelci-labgrid's agent (service) | Its own SSH key, logging in as itself to the local exporter. Groups: `labgrid`, `dialout`, `plugdev` (+ `kvm` on QEMU labs). Home `0700` (SSH key); tokens and config in `/etc/labgrid-agent` (`root:labgrid-agent`, `0750`/`0640`); checkouts `/opt/openwrtci-lab` `0750`. State `/var/lib/labgrid-agent` stays readable (`0755`): it holds firmware, logs and results, and the TFTP server must read the firmware labgrid symlinks there. |
| `labgrid-exporter` | system, no login | runs `labgrid-exporter` (and the ser2net instances it starts) | device groups; reads `/etc/labgrid/exporter.yaml` (root-owned) |
| `labgrid-coordinator` | system, no login | runs `labgrid-coordinator` | its state directory `/var/lib/labgrid-coordinator` (`places.yaml`) |

**`labgrid-dev` can't see the agent's credentials:**
- it isn't in the `labgrid-agent` group;
- the agent's home (SSH key), config and tokens aren't world-readable. Its
  state (firmware, logs, results) is readable on purpose, because the TFTP
  server serves firmware straight from there;
- the agent gets its tokens from `/etc/labgrid-agent/config.toml`
  (`root:labgrid-agent`, `0640`), never from its command line or environment;
- `labgrid-dev` isn't in `adm`/`systemd-journal`, so it can't read the
  agent's logs in the system journal (`journalctl -u labgrid-agent`);
- the role sets both accounts' group lists exactly, so groups granted by hand
  are dropped on the next run.

**What the two accounts share** (group `labgrid`):

| Path | Owner / mode | Why |
|---|---|---|
| `/srv/tftp`, `/srv/tftp/<device>` | `root:labgrid 2775` | TFTP staging symlinks (`ln -sfn`); setgid, no sticky bit, so either user can replace a symlink |
| `/var/cache/labgrid` | `root:labgrid 2775` | labgrid stages firmware in `/var/cache/labgrid/<user>/` (each user creates its own); files older than 7 days expire via tmpfiles.d |
| `/usr/local/sbin/labgrid-bound-connect` | sudo for `%labgrid` | SSH to `%vlanNNN` `NetworkService` addresses |

## Tools

labgrid, usbsdmux and pdudaemon are installed with `uv tool install`:
- venvs in `/opt/uv/tools`, entry points in `/usr/local/bin`, and any Python
  uv downloads in `/opt/uv/python`;
- labgrid is pinned to the commit in `uv.lock` (`labgrid_uv_tools` in
  `roles/labgrid_lab/defaults/main.yml`), so the exporter and coordinator run
  what the tests use. **Bump both together;**
- changing a tool's source spec reinstalls that tool and restarts the daemons.

## Per-lab data

Kept in `labs/<lab>/` at the repo root (`<lab>` is the inventory host name)
and shared with `podman/config-sync.sh`:
- `lab.yaml`: maintainers, access, devices, probes, maintenance tags;
- `exporter.yaml` (Jinja: comment with `##`);
- optional `dnsmasq.conf`, `pdudaemon.conf`;
- `README.md`: the lab's wiring for humans (not deployed).

The generic `files/exporter/{netplan.yaml,dnsmasq.conf,pdudaemon.conf,usbsdmux.rules}`
apply when a lab has no file of its own.

## Network

The role writes **only the DUT VLANs**, to `/etc/netplan/labnet.yaml`
(`files/exporter/netplan.yaml`: the VLANs in `labgrid_dut_vlans`, by default
`vlan101`–`vlan124` and `vlan200`, on `vlan_interface`). The host's own network (management port, Wi-Fi, …) is the
lab admin's, in the host's own netplan file, and must define
`vlan_interface`.
- If `labnet.yaml` on the host still configures anything besides VLANs, which
  earlier versions wrote there from `host_vars`' `netplan_custom`, the role
  stops instead of overwriting it.
- `netplan generate` checks the combined config before `netplan apply` runs.

## Migrating an existing lab host

The first run of the new `lab.yml`:
- moves the coordinator and exporter to their own accounts;
- moves `places.yaml` to `/var/lib/labgrid-coordinator`. The old
  `/etc/labgrid/places.yaml` is left alone, and the coordinator is regenerated
  from `labs/<lab>/lab.yaml`;
- drops `labgrid-dev` from any other groups;
- replaces the global sudoers line with `/etc/sudoers.d/labgrid-bound-connect`;
- stops at the netplan step if `/etc/netplan/labnet.yaml` still holds the
  host's own network (from `netplan_custom`). Move the `ethernets`/`wifis`
  part to the host's own netplan file (e.g. `/etc/netplan/01-host.yaml`),
  then run again. `netplan_custom` in `host_vars` is no longer used;
- installs labgrid, usbsdmux and pdudaemon with uv and removes `/opt/pipx`.

The same run deploys the agent (on hosts with `labgrid_agent_lab_name`), so
it switches from logging in as `labgrid-dev` to logging in as itself.
Afterwards, the admin removes the agent's old key (comment `labgrid-agent
(openwrtci)`) from `labgrid-dev`'s `authorized_keys`: Ansible only manages
the GitHub key block there. openwrtci's own `lab.yml` is no longer used; its unattended-upgrades
config (`52openwrtci-unattended-upgrades`) is replaced by `labgrid_lab`'s.
