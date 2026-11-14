# nixos-probe: NixOS-based test probe

The third probe kind, next to the wired and Wi-Fi OpenWrt probes
(`contrib/labgrid-probe`, `docs/openwrt-probe.md`), for what OpenWrt can't
do as a probe: a DHCPv6 server with prefix delegation (kea), raw L2 and
scapy, and any other tool from nixpkgs. Any x86_64 or aarch64 machine with
one Ethernet port does, a lab host running NixOS too.

- On the probe: a NixOS module (`module.nix`) installs `nixos-probe` (one
  Python script, `nixos-probe.py`) and the tools, and lets the tests log in
  over SSH as root.
- In openwrt-tests: tests reach it with labgrid's `SSHDriver` through the
  `nixos_probe` fixture (`tests/nixos_probe_fixtures.py`) and run
  `nixos-probe` and their own commands in the DUT's slot.
- Users: `tests/test_nixos_probe_pd.py` (DHCPv6-PD on wan6, the delegated
  prefix on the LAN, routing through the DUT, release).

```
lab host ──SSH (root, test runner's key)──┐
                                          ▼
   probe trunk port: management untagged, DUT VLANs tagged
     ├─ mgmt (sshd)
     ├─ netns np-lan<N>: DUT LAN VLAN, SLAAC from the DUT's RAs,
     │        ▲          whatever the test runs (scapy, dhcpcd, ping, ...)
     │        ▼
     │   DUT LAN → DUT (odhcpd, firewall) → DUT WAN (odhcp6c)
     │        ▼
     └─ netns np-wan<N>: DUT WAN VLAN, 2001:db8:<wan>::1/64
                         radvd (M+O flags, default route),
                         kea-dhcp6: IA_NA from 2001:db8:<wan>::/64,
                         IA_PD /60s from 2001:db8:<wan>:100::/56
```

`<N>` is the DUT's LAN VLAN, `<wan>` its WAN VLAN in hex (VLAN 201:
`2001:db8:c9::/48`). The namespaces only see their own VLAN, so traffic
between them has to go through the DUT. The WAN side is IPv6 only and has
no internet uplink; the OpenWrt probe provides those.

## `nixos-probe`

Every command prints one JSON object, `{"error": "..."}` (exit 1) on
failure.

| Command | Does |
|---|---|
| `up --lan-vlan <N> [--trunk <dev>]` | creates slot `<N>`: `np-lan<N>` with VLAN `<N>` of the trunk as `eth0` (default trunk: the default route's port); replaces an existing slot |
| `up --parent <dev>` | without VLANs (a lab host): a private macvlan on `<dev>`, which must be in `parents`; slot id 4000 + ifindex |
| `wan-up --slot <N> --wan-vlan <M>` (or `--wan-parent <dev>`) `[--delegated-len 60]` | (re)creates `np-wan<N>` and starts radvd and kea-dhcp6 there; the reply's `ipv6` has `link`, `router`, `pool`, `pd_pool`, `delegated_len` |
| `leases --slot <N>` | kea's active leases: `[{type: na\|pd, address, duid, iaid, valid_lifetime, expire, ...}]` |
| `exec --slot <N> lan\|wan -- <cmd>` | runs `<cmd>` in the namespace (`ip netns exec`); its output, not JSON |
| `info [--slot <N>]` | the slot's state, addresses and daemons, or the list of slots |
| `down --slot <N>` | stops the daemons, deletes the namespaces (and with them the VLAN devices) |

State, configs and logs (`kea-dhcp6.log`, `radvd.out`, the lease file) are
in `/run/nixos-probe/<N>/`. The WAN side doesn't route the delegated prefix
on its own: a test that needs the way back adds the route, as an ISP's
router does: `exec --slot <N> wan -- ip -6 route replace <prefix> via <wan6
address> dev eth0`.

In tests, through the fixtures:

```python
from nixos_probe_fixtures import nixos_probe, nixos_slot, nixos_wan_slot  # noqa: F401


def test_something(nixos_wan_slot, shell_command):
    slot = nixos_wan_slot  # slot.prefix: the DUT's delegated prefix
    slot.lan_check("ping -6 -c 1 2001:db8:c9::1")
    slot.scapy(
        "lan",
        "print(srp1(Ether()/IPv6(dst='ff02::2')/ICMPv6ND_RS(), iface='eth0', timeout=5))",
    )
```

## Install

1. Install NixOS on the machine, then add the module to its configuration,
   e.g. with this directory's flake:

   ```nix
   # flake.nix of the probe
   inputs.openwrt-tests.url = "github:aparcar/openwrt-tests?dir=contrib/nixos-probe";
   # in the nixosSystem's modules:
   openwrt-tests.nixosModules.default
   {
     services.nixos-probe = {
       enable = true;
       authorizedKeys = [ "ssh-ed25519 AAAA... labgrid@labgrid-<lab>" ];
     };
   }
   ```

   `example.nix` is a complete configuration to start from. Without
   flakes: `imports = [ /path/to/openwrt-tests/contrib/nixos-probe/module.nix ];`.

2. Switch port: management untagged, every DUT LAN and WAN VLAN tagged
   (like the OpenWrt probe). On a lab host that is the probe, list its DUT
   interfaces in `services.nixos-probe.parents` instead.

3. `nixos_probe: true` in the lab's `labnet.yaml` entry (place
   `labgrid-<lab>-nixos-probe`, tag `role=nixos-probe`) and the probe's SSH
   in its `exporter.yaml`:

   ```yaml
   labgrid-<lab>-nixos-probe:
     NetworkService:
       address: 192.168.128.4
       username: root
   ```

   `authorizedKeys` takes the key of the user that runs pytest (the KernelCI
   agent on the lab host).

| Option | Default | |
|---|---|---|
| `services.nixos-probe.enable` | `false` | |
| `services.nixos-probe.trunk` | `null` (default route's port) | port carrying the tagged DUT VLANs |
| `services.nixos-probe.parents` | `[]` | interfaces slots may use as macvlan parents |
| `services.nixos-probe.authorizedKeys` | `[]` | SSH keys for root |

## Testing the probe

`test.nix` is a NixOS VM test: the probe and a stand-in DUT (odhcp6c on its
WAN VLAN, radvd announcing the first /64 of the delegated prefix on its LAN
VLAN) on one trunk. It checks the slot, the leases, SLAAC behind the DUT,
scapy in a namespace, routing through the DUT and `down`.

```shell
nix build ./contrib/nixos-probe#checks.x86_64-linux.vm -L
```

## Limits

- Tests log in as root: anyone with an authorized key owns the probe. Keep
  it on the lab management network.
- One slot per DUT LAN VLAN; Linux allows one VLAN device per ID and port,
  so the OpenWrt probe and the NixOS probe can't share a trunk port.
- IPv6 only on the WAN side, no DNS there, no internet uplink.
