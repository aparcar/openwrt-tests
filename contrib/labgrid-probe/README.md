# labgrid-probe: OpenWrt-based test probe

A GL.iNet GL-MT2500(A) running OpenWrt. It acts as a client behind a DUT and
as the server in front of it.
- On the probe: a ucode module plus an rpcd plugin expose it as the ubus
  object `labgrid-probe`.
- In openwrt-tests: a labgrid driver (`drivers/labgridprobedriver.py`) calls
  that object as JSON-RPC over HTTP (uhttpd `/ubus`). Tests use it through the
  `probe` fixture (`tests/probe_fixtures.py`).
- First user: `tests/test_probe_lan.py` (DHCP, DNS, SLAAC, TCP and UDP
  throughput through the DUT). Wi-Fi comes later.

```
lab host ──HTTP /ubus (port forward)──┐
                                      ▼
   probe eth0 (one cable): management untagged, DUT VLANs tagged
     ├─ mgmt 192.168.128.3 (rpcd, uhttpd)
     ├─ netns pb-lan<N>: DUT LAN VLAN, DHCP client, iperf3 -c
     │        ▼
     │   DUT LAN 192.168.1.1 → NAT → DUT WAN
     │        ▼
     └─ netns pb-wan<N>: DUT WAN VLAN, 192.168.<wan>.2, iperf3 -s,
                         DHCP server for the DUT's WAN (router .1 = lab host)
```

The two namespaces only see their own VLAN, so traffic between them has to go
through the DUT. Nothing is configured per DUT on the probe: callers pass the
VLANs.

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
| `files/usr/share/ucode/labgrid_probe.uc` | All logic: slots, namespaces, DHCP, DNS, addresses, ping, WAN side, speedtests |
| `files/usr/share/rpcd/ucode/labgrid-probe.uc` | rpcd plugin: ubus object `labgrid-probe` |
| `files/usr/share/rpcd/acl.d/labgrid-probe.json` | rpcd's unauthenticated session may call exactly these methods |
| `files/usr/share/udhcpc/labgrid-probe.script` | udhcpc hook: configures the namespace, writes the lease as JSON |
| `files/etc/config/network` | `mgmt` on `eth0` (`192.168.128.3`), `lan` on `eth1` (`192.168.1.1` + DHCP, config/rescue port) |
| `files/etc/config/firewall` | Input from `lan` and `mgmt` only, nothing forwarded |
| `files/etc/config/system` | Hostname `labgrid-probe` |
| `files/etc/uci-defaults/90-labgrid-probe` | dnsmasq/odhcpd for the config port only (stays `sh`: sourced at boot) |
| `deploy.sh` | Copies the files and checks rpcd, the ACL and uhttpd |

## Install

1. Flash OpenWrt (`mediatek/filogic`, profile `glinet_gl-mt2500`, or
   `glinet_gl-mt2500-airoha` for the Airoha-PHY revision).
2. Install the packages: `ip-full iperf3 ucode ucode-mod-fs ucode-mod-uci rpcd
   rpcd-mod-ucode uhttpd uhttpd-mod-ubus` (`deploy.sh -p` does this; it needs
   internet on the probe).
3. Deploy. SSH as root is for the admin only; tests never log in:

   ```shell
   contrib/labgrid-probe/deploy.sh -c -r 192.168.1.1   # fresh device: code + config
   contrib/labgrid-probe/deploy.sh 192.168.128.3       # later: code only
   ```

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
| `up` | `trunk` (default: the default route's port, else UCI `mgmt`'s device) | slot details (`trunk`, `trunk_source`, `lan_mac`, `hostname`, …) |
| `wan_up` | `wan_vlan`, `wan_ip` (default `192.168.<wan_vlan>.2`) | slot details; starts iperf3 server and the DUT's WAN DHCP (`.100`–`.200`, router `.1`) |
| `down` | | `{ ok: true }`, also for half-built slots |
| `info` | | slot details and namespace state, or `{ not_up: true }` |
| `dhcp` | | `{ lease: { ip, mask, router[], dns[], domain, serverid, lease } }` |
| `addresses` | | `{ ipv4: [...], ipv6: [{ address, prefixlen, scope }] }` |
| `ping` | `host` (default `192.168.1.1`), `count` | `{ rc, transmitted, received, out }` |
| `resolve` | `name`, `server` (default `192.168.1.1`) | `{ rc, addresses[], out }` |
| `speedtest_start` | `duration` (s), `reverse` (WAN→LAN), `streams`, `udp`, `bitrate` (iperf3 `-b`, `"100M"`) | `{ started: true, … }` |
| `speedtest_result` | | `{ running: true }` or `{ rc, sent_bps, received_bps, cpu, reverse, udp }` plus `retransmits` (TCP) or `jitter_ms, lost_packets, packets, lost_percent` (UDP) |

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
  the ACL limits to the `labgrid-probe` object.
