# OpenWrt probes: testing a DUT's LAN, WAN and Wi-Fi

Many OpenWrt features only show with something on the other end of a cable
or radio: a DHCP client on the LAN, an upstream on the WAN, Wi-Fi clients.
A lab provides that with OpenWrt devices of its own, the probes. Tests drive
them over HTTP (uhttpd `/ubus`, rpcd's unauthenticated session), and each
probe is a labgrid place that tests reserve themselves.

| Probe | Hardware | Sits | Place / tag | Tests |
|---|---|---|---|---|
| wired probe | GL.iNet GL-MT2500, or an OpenWrt lab host itself | on the DUT's LAN and WAN VLANs | `labgrid-<lab>-probe`, `role=probe` | `test_probe_*.py`, `test_apk.py` |
| Wi-Fi probe | TP-Link TL-WDR4300 (ath9k) | in radio range of the DUT | `labgrid-<lab>-wifi-probe`, `role=wifi-probe` | `test_wifi_clients.py` |

Both are stock OpenWrt releases with the code in `contrib/labgrid-probe/`;
its README has the internals (ubus API, namespaces and routing, MAC scheme).
QEMU targets and labs without a probe skip these tests. The Debian probe
(`docs/debian-probe.md`) is a third kind, for what OpenWrt can't do (raw L2,
scapy, DHCPv6-PD).

## The wired probe: LAN and WAN

The probe is a LAN client behind the DUT and the DUT's upstream in front of
it. Per DUT it builds a slot of two network namespaces on the DUT's VLANs:

```
            probe
  ┌─────────────────────────────┐
  │ pb-lan<N>  (DUT LAN VLAN)   │ ── DUT LAN ──┐
  │   DHCP/DNS/SLAAC client,    │              │
  │   iperf3 client, RA capture,│            DUT (NAT, dnsmasq, odhcpd,
  │   multicast joiner          │              │  igmpproxy, firewall)
  │ pb-wan<N>  (DUT WAN VLAN)   │ ── DUT WAN ──┘
  │   DHCP server (router: the  │
  │   probe), iperf3 server,    │
  │   NTP, multicast sender,    │
  │   NAT uplink to the internet│
  └─────────────────────────────┘
```

The namespaces only see their own VLAN, so traffic between them has to cross
the DUT. The DUT's VLANs come from its own place: the LAN VLAN from its
`NetworkService` (`192.168.1.1%vlan<N>`), the WAN VLAN from its `wan`
`NetworkInterface` in `exporter.yaml`.

### Fixtures (`tests/probe_fixtures.py`)

| Fixture | Gives |
|---|---|
| `probe` | the probe's `LabgridProbeDriver`, reserved and locked for the module |
| `slot` | this DUT's slot (LAN side up); skips without a DUT VLAN |
| `wan_slot` | `slot` with the WAN side up (DHCP server, iperf3 server, NAT uplink); waits for the DUT's WAN lease; skips without a cabled WAN port |
| `lease` | the probe's DHCPv4 lease from the DUT's LAN |
| `online` | the DUT is online through its WAN (resolves `downloads.openwrt.org`, clock set); tests using it need the feature `online` |

### Tests

LAN side (`test_probe_lan.py`, `test_probe_ra.py`):

| Test | Checks |
|---|---|
| `test_lan_dhcp_lease` | the DUT's lease names itself as router and DNS |
| `test_lan_lease_on_dut` | dnsmasq lists the lease with the client's MAC and hostname |
| `test_lan_ping_dut`, `test_lan_dns_dhcp_hostname` | the client reaches the DUT; dnsmasq resolves the hostname from the DHCP request |
| `test_lan_ipv6_slaac` | the DUT's RAs give the client an address |
| `test_lan_ra`, `test_ra_prefix_lifetimes`, `test_odhcpd_ra_with_device_default_route` | parsed RAs: prefix flags and lifetimes, `ra_useleasetime` (#10804), a device default route (#23246) |
| `test_lan_soak_no_tx_timeout[upload,download]` | TCP against the DUT's own iperf3 server, no NETDEV WATCHDOG (#20570); `PROBE_SOAK_SECONDS` |

Through the DUT, LAN to WAN (`test_probe_lan.py`, `test_probe_multicast.py`):

| Test | Checks |
|---|---|
| `test_wan_reachable` | the probe's WAN-side server answers through the DUT's NAT |
| `test_speedtest[upload,download]`, `test_speedtest_udp[...]` | TCP throughput, UDP loss and jitter through the NAT (measured values to `record_property`) |
| `test_igmp_proxy_forwards`, `test_igmp_snooping_bridge` | igmpproxy forwards a group joined on the LAN from the WAN (#12366), also with IGMP snooping on br-lan (#9115) |

WAN side (`test_probe_wan.py`, `test_probe_uplink.py`, `test_apk.py`):

| Test | Checks |
|---|---|
| `test_wan_dhcp_broadcast_option` | the WAN address uses DHCP option 28 (#24782, xfail) |
| `test_ntp_synced_from_wan_dhcp` | sysntpd syncs to the NTP server from DHCP option 42 (#10843) |
| `test_uplink_ipv4`, `test_uplink_ipv6` | WAN address and default route, ping, DNS through the DUT, HTTPS (IPv6 skips: the probe's WAN side is IPv4 only) |
| `test_apk_*`, `test_owut_check` | package installs and owut with the probe as uplink |

The WAN tests need the target feature `wan_port` (WAN cabled to the probe),
the internet ones also `online`.

### Setting it up

A probe of its own (GL-MT2500): flash the release and run `sh
contrib/labgrid-probe/install-probe.sh lan --config root@<probe> -J
labgrid-<lab>` (trunk mode; drop `--config` for updates, which copy the code
only). Not Ansible: the probe has no python3. Its port is a trunk:
management untagged, every DUT LAN and WAN VLAN tagged.

On an OpenWrt lab host: `install-probe.sh local --parent <dev>... root@<lab
host>` (local mode: slots are macvlans on the host's own DUT interfaces).

Either way: `probe: true` in the lab's `labnet.yaml` entry and the probe's
uhttpd in its `exporter.yaml`:

```yaml
labgrid-<lab>-probe:
  NetworkService:
    address: 192.168.128.3   # 127.0.0.1, port 8080 on a lab host
    port: 80
    username: root
```

## The Wi-Fi probe: clients for the DUT's AP

The TL-WDR4300's two ath9k radios (2.4 GHz and 5 GHz, 802.11n) each run up
to about 64 station interfaces on one channel, so the probe joins the DUT's
AP with one or many clients at once, gets DHCP leases from the DUT and pings
through it. `tests/test_wifi_hwsim.py` covers the configuration side on QEMU
(`docs/hwsim.md`); these tests cover real clients on real radios.

### Fixtures (`tests/wifi_probe_fixtures.py`)

- `wifi_probe`: the probe's `LabgridWifiProbeDriver`, reserved and locked
  for the module. It boots the DUT first, so the shared probe isn't held
  during a boot.
- `WifiBed.start(ssh_command, wifi_probe, target, band, encryption,
  count=...)`: opens an AP on the DUT with an SSID of its own and starts the
  stations, locked to its BSSID, so other DUTs in range don't interfere.
  `wait()`, `ping()`, `dut_clients()` (hostapd's view).

### Tests (`test_wifi_clients.py`)

Each test runs per band the target declares (`wifi-2g`, `wifi-5g`) and the
probe has (no 6 GHz).

| Test | Checks |
|---|---|
| `test_wifi_client_encryption[band-ap-sta]` | 10 AP/client pairs (open, WPA2, WPA/WPA2 mixed, WPA2/WPA3 transition, WPA3): lease, ping, authorized on the DUT, MFP for SAE |
| `test_wifi_client_rejected[...]` | wrong passphrase (WPA2, WPA3) and WPA2-only on a WPA3-only AP stay out |
| `test_wifi_client_hidden_ssid` | a client joins a hidden SSID |
| `test_wifi_clients_many[encryption-band]` | 32 clients (`WIFI_PROBE_CLIENTS`), open and WPA2: all connect, get distinct leases, ping the DUT, show up in hostapd and `/tmp/dhcp.leases`; records association and lease times, DUT memory per client |
| `test_wifi_clients_isolation` | two clients reach each other through the AP; with `option isolate 1` they don't |
| `test_wifi_clients_maxassoc` | with `option maxassoc 4`, exactly 4 of 6 clients get in |
| `test_wifi_reload_keeps_clients` | `wifi reload` without a config change disconnects nobody |
| `test_wifi_restart_clients_reconnect` | after `wifi down`/`wifi up` every client is back |
| `test_wifi_radio_disable_keeps_other_band` | disabling the 2.4 GHz radio drops only its clients |
| `test_wifi_clients_follow_channel_change` | clients find the AP again on channel 11 |
| `test_wifi_client_counters` | hostapd's per-client byte counters grow by at least the traffic |
| `test_wifi_client_on_dut_lan` | the lease is in the DUT's LAN with the DUT as router |

Failures carry their evidence: lease failures the probe's udhcpc log and the
DUT's DHCP log, counters and neighbours (over the serial console, which
works when SSH doesn't); client-to-client failures hostapd's packet
counters, the probe's own counters and the AP's isolation settings.

### Setting it up

1. Flash the release's `ath79/generic` image for `tplink_tl-wdr4300-v1`,
   give LAN a static address on the lab management network without a DHCP
   server, cable it to an untagged management port, and place it in radio
   range of the lab's Wi-Fi DUTs.
2. `sh contrib/labgrid-probe/install-probe.sh wifi root@<probe> -J
   labgrid-<lab>`. Not Ansible: python3 doesn't fit in its 8 MB of flash. It
   disables the radios for netifd, adds the firewall zone for the stations
   and sets ath9k's software crypto (one reboot).
3. `wifi_probe: true` in the lab's `labnet.yaml` entry, and in its
   `exporter.yaml`:

   ```yaml
   labgrid-<lab>-wifi-probe:
     NetworkService:
       address: <probe>
       port: 80
       username: root
   ```

## Running the tests

On a lab device, with `scripts/lab-pytest.sh` (checks nobody holds the
place, locks it, runs pytest, powers off and unlocks):

```shell
sh scripts/lab-pytest.sh labgrid-aparcar openwrt_one \
    /abs/openwrt-25.12.6-mediatek-filogic-openwrt_one-initramfs.itb \
    tests/test_probe_lan.py tests/test_probe_wan.py tests/test_wifi_clients.py \
    -v --lg-log /abs/lg-log --junit-xml /abs/lg-log/results.xml
```

Don't lock a probe by hand: the fixtures reserve it through the coordinator,
so tests of other DUTs queue for it (`labgrid-client reservations`, `who`).
A probe held by a killed run shows its DUT place in its comment
(`held for <place>`); unlock it with `labgrid-client -p <probe place>
unlock`. Deploy new places only when the lab is idle: `uv run
ansible-playbook lab.yml --limit labgrid-<lab> --tags export` restarts the
coordinator.

## Pitfalls

- **The lab host's address is in the DUT's DHCP pool.** Lab hosts have
  `192.168.1.<VLAN id>` on each DUT VLAN, inside dnsmasq's default pool
  (`.100`-`.249`). A client that gets offered it points the DUT's ARP entry
  at itself and kills the lab's SSH connection to the DUT. One wired client
  rarely hits it, 32 Wi-Fi clients always do: `WifiBed` moves the DUT's pool
  to `.150`-`.249` first.
- **ath9k hardware crypto** decrypts for only one station per AP (its key
  cache finds pairwise keys by the peer's address): the others associate but
  never see a DHCP offer. Open networks work. The Wi-Fi probe runs with
  `nohwcrypt=1`.
- **The Wi-Fi probe's firewall.** Station interfaces in no fw4 zone get new
  incoming traffic rejected with ICMP unreachable; replies to their own
  traffic pass. The install script adds the zone `wprobe` for `ws+`.
- **iwinfo (25.12)** prints hostapd's raw key management and ciphers
  (`WPA-PSK (CCMP)`), with no WPA/WPA2 or "mixed" wording; `iw dev` shows no
  SSID for stations.
- **Throughput** is bound by the probes' CPUs (both ends of an iperf3 run
  share the wired probe's port): results near the limit read as "at least".

## Known results

OpenWrt One, 25.12.6: every Wi-Fi client test passes except
`test_wifi_clients_isolation`. With `option isolate 1`, hostapd runs with
`ap_isolate=1`, but the AP's bridge port has `hairpin_mode=1`
(multicast-to-unicast is on by default) and `isolated=0`, so traffic between
clients goes up to the bridge and straight back out: the clients still reach
each other.

## Limits

- Wired: the WAN side and the internet uplink are IPv4 only; multicast is
  IPv4 only (no MLD).
- Wi-Fi: 802.11n clients only (no VHT/HE, 80/160 MHz or 6 GHz; an ath10k
  probe with the CT firmware would add 802.11ac); open air with fixed
  antennas, so signal levels aren't controlled and other networks share the
  channel.
- The DUTs' internet access through the wired probe is unrestricted, which
  includes the lab management network behind it.
