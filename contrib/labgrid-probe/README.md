# labgrid-probe: OpenWrt-based test probe

A GL.iNet GL-MT2500(A) running OpenWrt. It acts as a client behind a DUT and
as the server in front of it.
- On the probe: a ucode module plus an rpcd plugin expose it as the ubus
  object `labgrid-probe`.
- In openwrt-tests: a labgrid driver (`drivers/labgridprobedriver.py`) calls
  that object as JSON-RPC over HTTP (uhttpd `/ubus`). Tests use it through the
  `probe` fixture (`tests/probe_fixtures.py`).
- Users: `tests/test_probe_lan.py` (DHCP, DNS, SLAAC, TCP/UDP throughput,
  LAN soak against the DUT), `test_probe_wan.py` (WAN DHCP options, NTP),
  `test_probe_ra.py` (parsed RAs), `test_probe_multicast.py` (IGMP proxy and
  snooping) and the online tests in `test_apk.py` (internet over the DUT's
  WAN). Wi-Fi comes later.

```
lab host ──HTTP /ubus (port forward)──┐
                                      ▼
   probe eth0 (one cable): management untagged, DUT VLANs tagged
     ├─ mgmt 192.168.128.3 (rpcd, uhttpd) ──→ lab host NAT → internet
     │                                ▲ fw4: forward + masquerade
     ├─ netns pb-lan<N>: DUT LAN VLAN, DHCP client, iperf3 -c, RA capture,
     │        ▲          multicast joiner                   │
     │        ▼                                             │ pbx<N> (veth)
     │   DUT LAN 192.168.1.1 → NAT → DUT WAN                │
     │        ▼                                             │
     └─ netns pb-wan<N>: DUT WAN VLAN, 192.168.<wan>.2 ─────┘ (masquerade)
                         DHCP server for the DUT's WAN (router: the probe),
                         iperf3 -s, NTP server, multicast sender
```

The two namespaces only see their own VLAN, so traffic between them has to go
through the DUT. Nothing is configured per DUT on the probe: callers pass the
VLANs.

The DUT gets internet on its WAN port, without restrictions, like a router
behind any uplink: the probe's WAN namespace is its router and masquerades
onto a veth into the root namespace (`pbv<N>` → `pbx<N>`, a `/30` from
`100.64.0.0/10` per slot), and fw4 forwards that out of `mgmt` with another
masquerade (zone `dut_wan` in `files/etc/config/firewall`). The DHCP server
hands out `1.1.1.1` and `9.9.9.9` as DNS servers. So apk, owut and
uclient-fetch on the DUT just work, nothing to configure there.

## In labgrid

The probe is a place of its own, `labgrid-<lab>-probe`, tagged `role=probe`.
The lab's `labnet.yaml` entry says `probe: true`, and its `exporter.yaml`
exports the HTTP endpoint:

```yaml
labgrid-aparcar-probe:
  NetworkService:
    address: 192.168.128.3
    port: 80          ## uhttpd /ubus
    username: root    ## required by NetworkService, unused
```

The `probe` fixture (module scope) does this:
1. reserves a `role=probe` place and waits for it;
2. locks it;
3. builds a probe target (`RemotePlace` + `LabgridProbeDriver`);
4. holds it for the whole test module, then unlocks and cancels the reservation.

Other modules wait at the coordinator in the meantime (`labgrid-client
reservations`, `who`). With more probes, the coordinator hands out a free one.
Labs without a `role=probe` place skip these tests.

The DUT's VLANs come from its own place: the LAN VLAN from its
`NetworkService` (`192.168.1.1%vlan<N>`), the WAN VLAN from its `wan`
`NetworkInterface`.

## Files

| Path | Purpose |
|---|---|
| `files/usr/share/ucode/labgrid_probe.uc` | All logic: slots, namespaces, DHCP, DNS, addresses, ping, WAN side (DHCP options, NTP, NAT uplink), speedtests, RA capture, multicast |
| `files/usr/share/ucode/labgrid_probe_ra.uc` | Router Advertisement parser (pure, no sockets) |
| `files/usr/libexec/labgrid-probe/ra-capture` | Sends a Router Solicitation, prints the RAs it receives as JSON (runs in the LAN namespace) |
| `files/usr/libexec/labgrid-probe/mcast` | IPv4 multicast joiner/sender, keeps a JSON status file (runs in a slot namespace, in the background) |
| `files/usr/share/rpcd/ucode/labgrid-probe.uc` | rpcd plugin: ubus object `labgrid-probe` |
| `files/usr/share/rpcd/acl.d/labgrid-probe.json` | rpcd's unauthenticated session may call exactly these methods |
| `files/usr/share/udhcpc/labgrid-probe.script` | udhcpc hook: configures the namespace, writes the lease as JSON |
| `files/etc/config/network` | `mgmt` on `eth0` (`192.168.128.3`), `lan` on `eth1` (`192.168.1.1` + DHCP, config/rescue port) |
| `files/etc/config/firewall` | Input from `lan` and `mgmt` only; forwards the DUTs' WAN uplinks (`pbx+`, zone `dut_wan`) to `mgmt`, masqueraded |
| `files/etc/config/system` | Hostname `labgrid-probe` |
| `files/etc/uci-defaults/90-labgrid-probe` | dnsmasq/odhcpd for the config port only (stays `sh`: sourced at boot) |

## Install

With Ansible, role `labgrid_probe`: it installs the packages, copies the
files, compile-checks the ucode, restarts rpcd and checks the ubus object,
the ACL and uhttpd.

1. Flash OpenWrt (`mediatek/filogic`, profile `glinet_gl-mt2500`, or
   `glinet_gl-mt2500-airoha` for the Airoha-PHY revision) and give root an
   SSH key. SSH as root is for the admin only; tests never log in.
2. Add the probe to group `probes` in `ansible/inventory.yaml`, reached
   through its lab host (`ansible_ssh_common_args: -J labgrid-<lab>`). For
   the first install, also `labgrid_probe_config: true`: it replaces the
   probe's network, firewall and hostname config, which applies at the
   next reboot (then `192.168.128.3` on `eth0`).
3. Run it (from `ansible/`); later runs update the code only:

   ```shell
   uv run ansible-playbook lab.yml --limit labgrid-<lab>-probe
   ```

The package install needs internet on the probe. `dnsmasq`, busybox `ntpd`
and `nft` (fw4) come with the default image.

## On the lab host

An OpenWrt lab host (role `labgrid_lab_openwrt`) can be the probe itself:
`labgrid_probe_on_host: true` in the inventory, `probe: true` in
`labnet.yaml`, and in `exporter.yaml`:

```yaml
labgrid-<lab>-probe:
  NetworkService:
    address: 127.0.0.1
    port: 8080        ## the probe's own uhttpd instance, loopback only
    username: root
```

`lab.yml` then installs it in local mode (UCI `labgrid_probe.main.mode`):

- **Slots are macvlans** on the host's own DUT interfaces, not VLANs on a
  trunk: the host already has `vlan<N>` (Linux allows one VLAN device per ID
  and port), or the DUT is cabled straight to a port (`eth1`) without VLANs.
  Each slot's namespace gets a macvlan in private mode, so it only talks to
  the DUT, not to the host's address on that interface.
- **Only the host's DUT interfaces** may be parents (`list parent`, from the
  DUTs' `NetworkService` `%<dev>` and `NetworkInterface`s in
  `exporter.yaml`), so callers can't attach slots to the uplink.
- **Slot ids:** the VLAN for `vlan<N>`, else 4000 + the interface's ifindex
  (`up` returns it as `lan_vlan`, which the fixtures then use).
- **HTTP:** a uhttpd instance of its own on `127.0.0.1:8080`; the host's
  other uhttpd config (LuCI) stays as it is. The DUTs' internet uplinks
  (`pbx+`) are forwarded to the host's `wan` zone
  (`labgrid_probe_uplink_zone`); WAN tests need the DUT's WAN port on a
  `vlan<N>` interface of the host.
- **No probe config** (`files/etc`): the host's network and firewall are the
  lab admin's.

## Switch

- **Probe `eth0`:** management untagged, every DUT LAN and WAN VLAN tagged.
  The probe tags the DUT VLANs on the port its default route uses, i.e. the
  port that actually carries the management network, so the trunk follows the
  running config.
- **Probe `eth1`:** config/rescue port, `192.168.1.1` with DHCP. That doesn't
  clash with the DUTs' `192.168.1.0/24`, which only exists inside the
  namespaces.

## ubus API

Every method takes `lan_vlan`. `up` creates that slot, and the others use it.
Failures come back as a reply with an `error` string; `not_up: true` means
`up` wasn't called for that VLAN.

| Method | Extra args | Reply |
|---|---|---|
| `up` | `trunk` (default: the default route's port, else UCI `mgmt`'s device); on a lab host `parent` (the DUT interface), `lan_vlan` optional | slot details (`lan_vlan` = slot id, `trunk`, `trunk_source`, `lan_mac`, `hostname`, …) |
| `wan_up` | `wan_vlan`, `wan_ip` (default `192.168.<wan_vlan>.2`); on a lab host `wan_parent` (default `vlan<wan_vlan>`; a port without VLAN needs no `wan_vlan` and gets `172.16.<ifindex>.2`) | slot details; starts iperf3 server, the NAT uplink and the DUT's WAN DHCP (`.100`–`.200`, router `wan_ip`, DNS `1.1.1.1`, `9.9.9.9`) |
| `down` | | `{ ok: true }`, also for half-built slots |
| `info` | | slot details and namespace state, or `{ not_up: true }` |
| `dhcp` | | `{ lease: { ip, mask, router[], dns[], domain, serverid, lease } }` |
| `addresses` | | `{ ipv4: [...], ipv6: [{ address, prefixlen, scope }] }` |
| `ping` | `host` (default `192.168.1.1`), `count` | `{ rc, transmitted, received, out }` |
| `resolve` | `name`, `server` (default `192.168.1.1`) | `{ rc, addresses[], out }` |
| `wan_dhcp` | `options` (list, dnsmasq syntax: `"28,192.168.101.127"`, `"option:mtu,1400"`), `ntp` (bool) | slot details plus `ntp_server`; restarts the WAN DHCP server with these options forced (replacing earlier ones), NTP server (busybox `ntpd -l`, stratum 1 from the probe's clock) on the WAN address announced as option 42. The DUT sees them at its next request |
| `speedtest_start` | `duration` (s, ≤ 3600), `reverse` (server→LAN), `streams`, `udp`, `bitrate` (iperf3 `-b`, `"100M"`), `server` (default: the WAN side; e.g. `192.168.1.1` for an `iperf3 -s` on the DUT), `port` | `{ started: true, … }` |
| `speedtest_result` | | `{ running: true }` or `{ rc, sent_bps, received_bps, cpu, reverse, udp }` plus `retransmits` (TCP) or `jitter_ms, lost_packets, packets, lost_percent` (UDP) |
| `ra_capture` | `timeout` (s, 1–20, default 5), `solicit` (default true), `count` (default 1, 0 = all within the timeout) | `{ ras: [{ router_lifetime, hop_limit, managed, other, preference, reachable_time, retrans_timer, mtu, source, source_lladdr, prefixes: [{ prefix, length, on_link, autonomous, valid_lifetime, preferred_lifetime }], routes, rdnss: [{ address, lifetime }], dnssl, after_ms }], solicited, timeout_ms }` |
| `mcast_join` | `group` (224.0.0.0/4), `port` (5001), `side` (`lan`), `igmp_version` (0 = kernel default, 2, 3) | `mcast_result`'s reply once joined; counts arrivals until `mcast_leave` |
| `mcast_send` | `group`, `port`, `side` (`wan`), `count` (10), `interval` (ms, 100), `ttl` (8) | `{ started: true, … }`; sends in the background |
| `mcast_result` | | `{ receiver: { joined, received, bytes, senders, first_seq, last_seq, running }, sender: { sent, errors, done, running } }` (null if not started) |
| `mcast_leave` | | stops receiver (IGMP leave) and sender, returns the final `mcast_result` |

The driver (`drivers/labgridprobedriver.py`) has a method per call that
takes the LAN VLAN first (`wan_dhcp`, `speedtest_start`, `speedtest` (start
and wait), `ra_capture`, `mcast_join`, …); the `Slot` from
`tests/probe_fixtures.py` binds the VLAN: `slot.ra_capture(timeout=5)`.

## Internet on the DUT

With `wan_up`, the DUT is online through its WAN port (see above). The
probe's management network must reach the internet (the lab host's NAT,
`192.168.128.1`). The forwarding needs the shipped firewall config
(`labgrid_probe_config: true`).

In tests, use the `online` fixture (`tests/probe_fixtures.py`, module
scope). It brings up the WAN side (`wan_slot`), waits until the DUT resolves
`downloads.openwrt.org`, sets the DUT's clock from the test host if it is
more than 60 s off (a build-date clock fails certificate checks). Request
it next to `ssh_command` and run commands there. Boards without a cabled
WAN port skip. After a reboot, call `wait_online(ssh_command)` again.
Tests using `online` need the labgrid feature `online` (conftest.py adds
`lg_feature("online")` to them): they skip unless the target lists it under
`features:`, next to `wan_port`, for a board whose WAN port is cabled to the
probe.

```python
# conftest.py only imports `probe`: import the fixtures a module uses
from probe_fixtures import online, slot, wan_slot  # noqa: F401


def test_something(online, ssh_command):
    ssh_command.run_check("apk update && apk add iperf3", timeout=300)
    ssh_command.run_check(
        "uclient-fetch -O /tmp/sysupgrade.bin https://downloads.openwrt.org/..."
    )
```

On the probe: `ubus call labgrid-probe info '{"lan_vlan":102}'`. From the lab
host, the same call over HTTP:

```shell
curl -s http://192.168.128.3/ubus -d '{"jsonrpc":"2.0","id":1,"method":"call",
  "params":["00000000000000000000000000000000","labgrid-probe","info",{"lan_vlan":102}]}'
```

## Limits

- Both iperf3 ends run on the probe and share its link. The ceiling is the
  probe's CPU and port, not the DUT's: read results near it as "≥ probe limit".
- Anyone on the lab management network can call the probe's methods, which
  the ACL limits to the `labgrid-probe` object. On a lab host, the ACL also
  covers any other uhttpd instance with `/ubus` (LuCI's), on the networks
  its firewall lets reach it.
- `ra_capture` and `wan_dhcp` block rpcd while they run (up to 20 s and
  ~1 s); rpcd's timeout is 30 s.
- Multicast is IPv4 only (no MLD), and `mcast_send` is a plain UDP sender:
  no IGMP querier on the WAN side. Raw L2, scapy, DHCPv6/PD and kea belong
  to the Debian probe (`contrib/debian-probe`).
- The DUTs' internet access is unrestricted, which includes the lab
  management network behind the probe (lab host, switch).
- The internet uplink is IPv4 only.
