# Wi-Fi tests on mac80211_hwsim (QEMU, no lab hardware)

`targets/qemu_x86-64.yaml` has the `hwsim` feature: virtual Wi-Fi radios
from the kernel's `mac80211_hwsim`, installed with apk when a test needs
them. Tests marked `lg_feature("hwsim")` use them through the `hwsim`
fixture (`tests/hwsim_setup.py`): `tests/test_wifi_hwsim.py` (Wi-Fi
configuration regressions) and `test_wifi_hwsim_sae_mixed` in
`tests/test_wifi.py`.

They cover wifi-scripts, hostapd and netifd logic, not drivers. A bug that
reproduces here is a configuration bug; one that only shows on a board is
probably the driver's.

## How it works

- **One VM.** hwsim delivers frames between all radios of one module load,
  whatever network namespace a radio is in, so no host-side medium is
  needed.
- **The DUT's radios** are configured through `/etc/config/wireless`, as on
  a board.
- **The peer** (a station or an "external" AP) is another hwsim wiphy that
  has been moved into a netns (`ip netns`, `iw phy … set netns`). It runs its
  own `wpa_supplicant`/`hostapd` from the wpad multicall binary. Its traffic
  goes over the simulated air and through the DUT's bridge. It does not
  short-cut through the DUT's own stack.
- **The `hwsim` fixture** (`tests/hwsim_setup.py`) runs the setup the first
  time a test needs it, over the serial console: it installs what the image
  lacks, (re)starts wpad and netifd, loads `mac80211_hwsim radios=2` and runs
  `wifi config`. Other tests on the target pay nothing. The result is kept
  per boot (`boot_id`), so it runs again after a reboot or `sysupgrade -n`.
  A failure (for example, no network for apk) is kept too: every hwsim test
  of that boot errors at once with the same message, without retrying the
  slow install.
- **Each test** reloads the module with the parameters it needs (`radios`,
  `multi_radio=1`, `mlo=1`) and starts from a fresh wireless config with
  every radio disabled and no wifi-iface. The fixture restores `/etc/config`
  and the default radios afterwards.

### Pitfalls of installing Wi-Fi at run time (25.12)

Each of these made every AP test fail with "AP did not come up" and an
empty `iw dev`, without any error from `reload_config`:

- **netifd has no wireless support.** netifd loads
  `/lib/netifd/wireless.uc` (from wifi-scripts) only when it starts. On the
  official x86-64 image it starts without it (syslog:
  `netifd: Wireless module not found`, no `network.wireless` ubus object).
  The setup restarts netifd after the install.
- **The first `reload_config` is silent.** It signals only configs listed
  in `/var/run/config.md5`, which is written at boot, before
  `/etc/config/wireless` existed. The setup runs `reload_config` once
  after `wifi config`, so the first test's apply is not that silent first one.
- **`wifi down` sticks.** It clears netifd's autostart for every radio,
  and a config reload does not set it again. `Bed.load()` runs `wifi up`
  after writing its fresh config.
- **Country '00'.** `wifi config` leaves the radios enabled (only their
  default wifi-iface is disabled), and since hwsim radios have every band,
  it makes them 6g with `country '00'`. wifi-scripts' `device_country_code()`
  (`/usr/share/ucode/wifi/hostapd.uc`) copies the country of *every*
  wifi-device, disabled or not, into each hostapd config (the last one
  wins), and hostapd refuses `country_code=00` ("Invalid country_code
  '00'"). `Bed.load()` therefore disables every radio and deletes its
  country.
- **wpad variants.** Tests switch between them with apk: `basic` is what
  boards ship, `full` has dynamic VLAN and MLO, `mesh` is basic plus
  802.11s. The module is ordered so that it switches only twice.

## Firmware: official image + apk at run time

Official x86-64 images have no Wi-Fi packages at all: no
`kmod-mac80211-hwsim`, no wpad, no `iw`, no `wifi-scripts` and no `ip-full`
(`profiles.json` of 25.12.5). The release feeds do have all of them. The
25.12.5 x86/64 kmods feed has `kmod-mac80211-hwsim`, and the base feed has
every wpad variant and `ip-full`. `kmod-cfg80211` pulls in `iw`,
`wifi-scripts` and `wireless-regdb`; the ucode `wifi-scripts` ship their own
`iwinfo`. So the setup installs `kmod-mac80211-hwsim ip-full
wpad-basic-mbedtls` with apk over QEMU's user-mode WAN (`eth1`), which has
internet access whenever the host does. The target runs QEMU with
`snapshot=on`, so none of this is written into the firmware file.

Why not a custom image:

- owci schedules **official** firmware per release. A custom image would
  need its own build per release and snapshot. It would also show up as
  `custom` in KCIDB, not under the release.
- The kmod comes from the release's own kmods feed, so it always matches
  the kernel.
- What we test (wifi-scripts, hostapd) is exactly the release's code.

Costs and limits:

- The host needs internet access (or a downloads.openwrt.org proxy reachable
  from the guest's WAN). Setup adds about 20–40 s per boot in which a
  hwsim test runs, plus about 10 s per wpad switch. GitHub Actions leaves
  the hwsim tests out for now (`-k "not hwsim"` in `.github/workflows/qemu.yml`).
- A **snapshot** image older than the current snapshot kmods feed can't get
  a matching kmod. owci schedules the newest snapshot, so this only affects
  manual runs of old snapshots.
- An image that already has hwsim and a wpad (for example, one built with
  `kmod-mac80211-hwsim wpad-basic-mbedtls ip-full`, passed as owci
  `--firmware`) skips apk. The setup only installs what is missing.
- Module parameters give **uniform** radios. Per-radio shapes need hwsim's
  netlink `NEW_RADIO`: a radio limited to some bands, to frequency ranges,
  or a split 5 GHz band. That needs a compiled helper, so a custom image.
  No current test needs those shapes.

## Run it

```shell
uv run pytest tests/test_wifi_hwsim.py tests/test_wifi.py -k hwsim \
    --lg-env targets/qemu_x86-64.yaml \
    --firmware openwrt-25.12.5-x86-64-generic-squashfs-combined.img \
    --lg-log --log-cli-level=CONSOLE
```

The QEMU host needs `qemu-system-x86_64`, ideally with KVM, and internet
access for the guest. Without the `hwsim` feature (any other target) the
tests are skipped.

### In openwrtci

The hwsim tests run wherever `qemu_x86-64` runs: no extra platform. The
results arrive through the JUnit XML like any other test, as module
`wifi_hwsim` (KCIDB `openwrt.wifi_hwsim.<test>`). The agent's health check
(`test_shell`, `test_ssh`) does not need hwsim: a failed apk install only
errors the hwsim tests.

## Tests

| Test | Issue | wpad | Radios |
|---|---|---|---|
| `test_wifi_ap_options[...]` | #23565 | basic | 2 |
| `test_wifi_unsupported_option_isolated[...]` | #24625 (xfail while upstream calls it documented) | basic | 2 |
| `test_wifi_txpower_zero` | #22855 | basic | 2 |
| `test_wifi_country_applies` | #13070 (reload only, no reboot) | basic | 2 |
| `test_wifi_acs_respects_channels[2g,5g]` | #22774 | basic | 2 |
| `test_wifi_multi_ssid_data[primary,secondary]` | #24293 (wifi-scripts part) | basic | 2, peer STA in netns |
| `test_wifi_vif_macs_unique` | #23578 | basic | 1 × `multi_radio` |
| `test_wifi_hwsim_dynamic_vlan_bridged` | #9944 | full | 2, peer STA + DHCP |
| `test_hostapd_survives_sta_reconnect_during_scan` | #25840 (approximation) | full | 2 × `multi_radio`, peer AP |
| `test_wifi_mlo_he_htmode` | #25414 | full | 1 × `multi_radio mlo` |
| `test_wifi_mlo_width_survives_other_radio_reload` | #25413 (80 MHz instead of 160, to avoid DFS) | full | 1 × `multi_radio mlo` |
| `test_wifi_mesh_he40_upper[6,8,11]` | #26335 | mesh | 2, both meshes in the DUT |

A test skips if the kernel's hwsim lacks `multi_radio`/`mlo`.

## When to use more than one VM

The single VM covers configuration bugs. A second VM, with a relay on the
host carrying the frames between them, is only worth it when:

- the AP and the station must be separate kernels (a crash or reboot on one
  side), or
- the test needs signal or loss control (roaming, weak links).

None of the tests above needs either. If one does, the design would be:

- a second QEMUDriver in the target, connected by a socket netdev;
- hwsim radios registered with `HWSIM_CMD_REGISTER` in each guest;
- a small relay on the QEMU host.
