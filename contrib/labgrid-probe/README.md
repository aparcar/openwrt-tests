# labgrid-probe: OpenWrt-based test probe

How the probes are used by the tests (fixtures, test overview, lab setup):
`docs/openwrt-probe.md`. This file covers the probe side.

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
  WAN). Wi-Fi clients come from a second probe, see [Wi-Fi probe](#wi-fi-probe).

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

With `install-probe.sh lan` over SSH (not Ansible: the probe is a plain
OpenWrt device without python3). It installs the packages, copies the
files, compile-checks the ucode, restarts rpcd and checks the ubus object,
the ACL and uhttpd.

1. Flash OpenWrt (`mediatek/filogic`, profile `glinet_gl-mt2500`, or
   `glinet_gl-mt2500-airoha` for the Airoha-PHY revision) and give root an
   SSH key. SSH as root is for the admin only; tests never log in.
2. The first install, with `--config`: it also replaces the probe's
   network, firewall and hostname config, which applies at the next reboot
   (then `192.168.128.3` on `eth0`):

   ```shell
   sh contrib/labgrid-probe/install-probe.sh lan --config root@<probe> -J labgrid-<lab>
   ```

3. Updates (after changing the files, or when the tests expect a newer
   probe: `up` answering ubus status 2 means an outdated one) copy the code
   only:

   ```shell
   sh contrib/labgrid-probe/install-probe.sh lan root@192.168.128.3 -J labgrid-aparcar
   ```

List the probe in group `probes` of `ansible/inventory.yaml` for its address
and jump host; no play uses that group. The package install needs internet
on the probe. `dnsmasq`, busybox `ntpd`
and `nft` (fw4) come with the default image.

## On the lab host

An OpenWrt lab host (role `labgrid_lab_openwrt`) can be the probe itself:
`probe: true` in `labnet.yaml`, and in `exporter.yaml`:

```yaml
labgrid-<lab>-probe:
  NetworkService:
    address: 127.0.0.1
    port: 8080        ## the probe's own uhttpd instance, loopback only
    username: root
```

Install and update it in local mode (UCI `labgrid_probe.main.mode`), with a
`--parent` for each DUT interface (the `%<dev>` of the DUTs'
`NetworkService` and the `ifname` of their `NetworkInterface`s):

```shell
sh contrib/labgrid-probe/install-probe.sh local --parent eth1 --parent eth0 root@<lab host>
```

- **Slots are macvlans** on the host's own DUT interfaces, not VLANs on a
  trunk: the host already has `vlan<N>` (Linux allows one VLAN device per ID
  and port), or the DUT is cabled straight to a port (`eth1`) without VLANs.
  Each slot's namespace gets a macvlan in private mode, so it only talks to
  the DUT, not to the host's address on that interface.
- **Only the host's DUT interfaces** may be parents (`list parent`, the
  `--parent`s), so callers can't attach slots to the uplink.
- **Slot ids:** the VLAN for `vlan<N>`, else 4000 + the interface's ifindex
  (`up` returns it as `lan_vlan`, which the fixtures then use).
- **HTTP:** a uhttpd instance of its own on `127.0.0.1:8080`; the host's
  other uhttpd config (LuCI) stays as it is. The DUTs' internet uplinks
  (`pbx+`) are forwarded to the host's `wan` zone; WAN tests need the DUT's WAN port on a
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
(`install-probe.sh lan --config`).

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

## Wi-Fi probe

A TP-Link TL-WDR4300 running a stock OpenWrt release joins a DUT's AP with
many clients ("stations") at once. Its two radios are ath9k: 2.4 GHz (AR9344,
2x2) and 5 GHz (AR9580, 3x3), 802.11n only. ath9k allows up to 2048 station
interfaces on one channel; measured on this probe: 64 WPA2 stations
associate in about 28 s (batches of 8) and cost about 225 kB of RAM each.

- On the probe: `files/usr/share/ucode/labgrid_probe_wifi.uc`, the ubus
  object `labgrid-wifi-probe` (rpcd plugin and ACL), the background helper
  `files/usr/libexec/labgrid-probe/wifi-sta` and the udhcpc hook
  `files/usr/share/udhcpc/labgrid-wifi-probe.script`.
- In openwrt-tests: `LabgridWifiProbeDriver` (`drivers/labgridprobedriver.py`),
  the `wifi_probe` fixture and `WifiBed` (`tests/wifi_probe_fixtures.py`),
  the tests in `tests/test_wifi_clients.py`.
- In labgrid: a place of its own, `labgrid-<lab>-wifi-probe`, tagged
  `role=wifi-probe` (`wifi_probe: true` in `labnet.yaml`); the exporter
  exports its uhttpd:

  ```yaml
  labgrid-aparcar-wifi-probe:
    NetworkService:
      address: 192.168.128.4
      port: 80
      username: root
  ```

### Install

1. Flash the release's `ath79/generic` image for `tplink_tl-wdr4300-v1`.
2. Give LAN a static address on the lab management network, without a DHCP
   server, and cable a LAN port to an untagged management port:
   `uci set network.lan.ipaddr=192.168.128.4`, `network.lan.gateway`,
   `network.lan.dns` (`192.168.128.1`), `uci set dhcp.lan.ignore=1`.
3. From the openwrt-tests checkout:

   ```shell
   sh contrib/labgrid-probe/install-probe.sh wifi root@192.168.128.4 -J labgrid-aparcar
   ```

   It copies the files, installs `ip-full`, disables both radios in
   `/etc/config/wireless` (netifd must leave them to the probe), adds the
   firewall zone `wprobe` for the stations (`ws+`, input accepted: fw4's
   default input policy would answer new traffic to a station, such as
   another station's ping, with ICMP unreachable), sets
   `ath9k nohwcrypt=1` (see below; reboots once for it), compile-checks the
   code, restarts rpcd and checks the ubus object. Not Ansible: that needs
   python3, which doesn't fit in 8 MB of flash.

**Software crypto.** All stations of a set talk to the same AP. ath9k's
hardware key cache finds a pairwise key by the peer's address, so with
hardware crypto only one station's unicast frames decrypt: the others
associate (the 4-way handshake is unencrypted) but never see a DHCP offer.
Open networks work either way. Hence `nohwcrypt=1`; the probe's CPU does
the crypto, which is fine at test traffic rates.

**The lab host's address in the DUT's LAN.** The lab host has
`192.168.1.<VLAN id>` on each DUT VLAN (`.102` on `vlan102`), inside the
DUT's default DHCP pool (`.100`-`.249`). Once dnsmasq offers that address to
a station, the DUT's ARP entry for it points to the station and the lab's
SSH connection to the DUT dies. `WifiBed` therefore moves the DUT's pool to
`.150`-`.249` first (`dut_dhcp_pool()` in `tests/wifi_probe_fixtures.py`).

`wifi-spike.sh` is the measurement script the design came from: run by hand
on the probe, no rpcd.

### Stations

`sta_up` creates `count` station interfaces `ws<n>s<i>` on the radio for the
band (n: phy number, i: station) and returns at once. The helper then:

- starts wpa_supplicant for `batch` stations per process (default 8),
  `stagger_ms` apart (default 1000), locked to `bssid` and `freq` if given;
- gives every connected station a DHCP lease from the DUT (at most 16
  udhcpc at once) and keeps doing so after reconnects;
- writes its progress to `state.json` until `sta_down` kills it.

A station counts as connected when its `operstate` is `up`: wpa_supplicant
sets it after the 4-way handshake. `carrier_changes` per station shows
disconnects. `iw dev` lists no SSID for stations on this kernel.

MACs: the radio's address, locally administered, byte 4 = n * 16 + i / 256,
byte 5 = i % 256. Only the last two bytes differ, which keeps ath9k's BSSID
mask (the addresses the hardware ACKs) narrow.

Routing: a cfg80211 interface can't move into a network namespace on its
own, so all stations are in the root namespace. Each lease is a /32 on its
station, with a routing table and a rule `from <ip> iif lo` of its own;
the local table's rule moves from preference 0 to 30000, behind those
rules. A ping from one station to another station's address therefore
leaves over the air and goes through the AP (client isolation tests).
Per station: `accept_local=1`, `arp_ignore=1`, `arp_announce=2`,
`rp_filter=0`, IPv6 off.

### ubus API

| Method | Args | Reply |
|---|---|---|
| `radios` | | `{ radios: [{ phy, bands, mac, busy, stations, up }], mem_available_kb }` |
| `sta_up` | `band` (`2g`, `5g`), `count` (1–256), `ssid`, `bssid`, `freq`, `encryption` (`none`, `psk`, `psk2`, `psk-mixed`, `sae`, `sae-mixed`), `key`, `hidden` (probe for the SSID), `batch`, `stagger_ms` | `{ phy, band, count, started, stations: [{ index, ifname, mac }] }` |
| `sta_status` | `band`, `detail` (`iw dev link` per station) | `{ phase, error, count, connected, leases, first_connected_s, all_connected_s, all_leases_s, mem_available_kb, stations: [{ index, ifname, mac, connected, carrier_changes, ip, lease, link }] }` |
| `sta_ping` | `band`, `host`, `count` (≤ 20), `size`, `stations` (indexes; default all) | `{ host, count, ok, results: [{ index, ifname, ip, rc, transmitted, received }] }`; all stations at once |
| `sta_down` | `band` (default: all) | `{ ok, down: [phys] }` |

`phase`: `creating`, `associating`, `running`, or `failed` with `error`.

### Limits

- 802.11n only: no VHT/HE/EHT features, no 80/160 MHz, no 6 GHz. HE and VHT
  DUTs serve these clients as HT.
- About 64 stations per radio is the tested range. The hardware key cache
  has 128 entries; with more stations crypto may fall back to software.
- Throughput is bound by the probe's 560 MHz CPU (roughly 100 Mbit/s for
  all stations together): not a throughput probe.
- Fixed antennas, open air: the signal level isn't controlled, and other
  networks in the lab share the channel.
