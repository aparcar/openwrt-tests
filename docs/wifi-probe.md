# Wi-Fi client tests with the Wi-Fi probe

`tests/test_wifi_clients.py` tests a DUT's access point with real clients: a
lab's Wi-Fi probe joins it with one or many stations at once, gets DHCP
leases from the DUT and pings through it. QEMU and labs without a Wi-Fi probe
skip these tests; `tests/test_wifi_hwsim.py` covers the configuration side on
QEMU (`docs/hwsim.md`).

The probe is a TP-Link TL-WDR4300 running a stock OpenWrt release. Its two
ath9k radios (2.4 GHz and 5 GHz, 802.11n) each run up to about 64 station
interfaces on one channel. How it works inside (ubus API, routing, MAC
scheme) is in `contrib/labgrid-probe/README.md`, section "Wi-Fi probe".

## Tests

Each test runs per band the target declares (`wifi-2g`, `wifi-5g`) and the
probe has (no 6 GHz). The DUT's AP gets an SSID of its own per test, and the
stations are locked to its BSSID, so other DUTs in range don't interfere.

| Test | Checks |
|---|---|
| `test_wifi_client_encryption[band-ap-sta]` | 10 AP/client pairs (open, WPA2, WPA/WPA2 mixed, WPA2/WPA3 transition, WPA3): the client gets a lease and pings the DUT, the DUT lists it as authorized, SAE clients use MFP |
| `test_wifi_client_rejected[...]` | wrong passphrase (WPA2, WPA3) and a WPA2-only client on a WPA3-only AP stay out |
| `test_wifi_client_hidden_ssid` | a client joins a hidden SSID |
| `test_wifi_clients_many[encryption-band]` | 32 clients (`WIFI_PROBE_CLIENTS`), open and WPA2: all connect, get distinct leases in the DUT's LAN, ping it, and show up in hostapd and `/tmp/dhcp.leases`; records association and lease times and the DUT's memory per client |
| `test_wifi_clients_isolation` | two clients reach each other through the AP; with `option isolate 1` they don't |
| `test_wifi_clients_maxassoc` | with `option maxassoc 4`, exactly 4 of 6 clients get in |
| `test_wifi_reload_keeps_clients` | `wifi reload` without a config change disconnects nobody |
| `test_wifi_restart_clients_reconnect` | after `wifi down` and `wifi up` every client is back |
| `test_wifi_radio_disable_keeps_other_band` | disabling the 2.4 GHz radio drops only its clients |
| `test_wifi_clients_follow_channel_change` | clients find the AP again after it moves from channel 1 to 11 |
| `test_wifi_client_counters` | hostapd's per-client byte counters grow by at least the traffic |
| `test_wifi_client_on_dut_lan` | the lease is in the DUT's LAN with the DUT as router |

Failures carry their evidence: lease failures the probe's udhcpc log and the
DUT's DHCP log, counters and neighbours (over the serial console, which
works when SSH doesn't); client-to-client failures hostapd's packet
counters, the probe's own counters and the AP's isolation settings.

## Running them

On a lab device, with `scripts/lab-pytest.sh` (checks nobody holds the
place, locks, runs, powers off and unlocks):

```shell
sh scripts/lab-pytest.sh labgrid-aparcar openwrt_one \
    /abs/openwrt-25.12.6-mediatek-filogic-openwrt_one-initramfs.itb \
    tests/test_wifi_clients.py -v --lg-log /abs/lg-log --junit-xml /abs/lg-log/results.xml
```

The `wifi_probe` fixture reserves the probe place (`labgrid-<lab>-wifi-probe`,
tag `role=wifi-probe`) itself: don't lock it by hand. It boots the DUT first,
so the shared probe isn't held during a boot. The whole module takes about 20
minutes.

## Setting up a lab

1. Flash the probe with the release's `ath79/generic` image for
   `tplink_tl-wdr4300-v1`, give LAN a static address on the lab management
   network without a DHCP server, and cable it to an untagged management
   port. Place it in radio range of the lab's Wi-Fi DUTs.
2. Install the probe code: `sh contrib/labgrid-probe/install-wifi-probe.sh
   root@<probe> -J labgrid-<lab>` (not Ansible: python3 doesn't fit in its
   flash). It also disables the radios for netifd, adds the firewall zone for
   the stations and sets ath9k's software crypto (one reboot).
3. `wifi_probe: true` in the lab's `labnet.yaml` entry (place
   `labgrid-<lab>-wifi-probe`), and its uhttpd in the lab's `exporter.yaml`:

   ```yaml
   labgrid-<lab>-wifi-probe:
     NetworkService:
       address: <probe>
       port: 80
       username: root
   ```

4. Deploy the places when the lab is idle (restarts the coordinator):
   `uv run ansible-playbook lab.yml --limit labgrid-<lab> --tags export`.

## Pitfalls found on the way

- **ath9k hardware crypto.** All stations talk to the same AP; ath9k's key
  cache finds pairwise keys by the peer's address, so only one station's
  unicast frames decrypt and the others never see a DHCP offer. Open
  networks work. The install script sets `nohwcrypt=1`.
- **The lab host's address is in the DUT's DHCP pool.** Lab hosts have
  `192.168.1.<VLAN id>` on each DUT VLAN, inside dnsmasq's default pool
  (`.100`-`.249`). Offering it to a station points the DUT's ARP entry at the
  station and kills the lab's SSH connection to the DUT. `WifiBed` moves the
  DUT's pool to `.150`-`.249` first.
- **The probe's firewall.** Station interfaces in no fw4 zone get new
  incoming traffic rejected (ICMP unreachable): replies to their own traffic
  pass, a ping from another station doesn't. The install script adds the zone
  `wprobe` for `ws+`.
- **`iw dev` shows no SSID for stations**; the probe counts a station as
  connected by its `operstate` (up after the 4-way handshake).
- **iwinfo (25.12)** prints hostapd's raw key management and ciphers
  (`WPA-PSK (CCMP)`), with no WPA/WPA2 or "mixed" wording.

## Known results

OpenWrt One, 25.12.6: every test passes except `test_wifi_clients_isolation`.
With `option isolate 1`, hostapd runs with `ap_isolate=1`, but the AP's
bridge port has `hairpin_mode=1` (multicast-to-unicast is on by default) and
`isolated=0`, so traffic between clients goes up to the bridge and straight
back out: the clients still reach each other.

## Limits

- 802.11n clients only: no VHT/HE features, 80/160 MHz or 6 GHz. An ath10k
  probe with the CT firmware would add 802.11ac clients.
- Throughput is bound by the probe's CPU; these are not throughput tests.
- Open air with fixed antennas: signal levels aren't controlled, and other
  networks in the lab share the channel.
