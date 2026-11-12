# iPhone Wi-Fi probe (iprobe)

An iPhone joins the DUT's access point as a real Wi-Fi client, to check
that Apple's Wi-Fi stack works with OpenWrt: WPA2, WPA3 and the mixed modes,
PMF, hidden SSIDs, and reconnecting after a Wi-Fi restart. It complements
the ath9k Wi-Fi probe (`docs/openwrt-probe.md`, many wpa_supplicant
stations) with one real client.

- On the host the phone is plugged into: `contrib/iphone-probe/iprobe.py`,
  a CLI that prints one JSON reply and exits 0 (ok), 1 or 2 (error).
- In openwrt-tests: `drivers/iphoneprobedriver.py` runs `iprobe` over SSH,
  and `tests/iphone_probe_fixtures.py` provides `iphone_probe` and
  `PhoneBed`.
- Tests: `tests/test_wifi_iphone.py`.

## How it works

The phone is not jailbroken. `iprobe` talks to it over USB with
[pymobiledevice3](https://github.com/doronz88/pymobiledevice3) through
usbmuxd:

- `iprobe join --ssid S --encryption E --key K [--hidden]` silently installs
  a configuration profile with one Wi-Fi payload. Silent installs need a
  **supervised** phone and the supervision keybag. The profile turns MAC
  randomization off, so the DUT sees the phone's hardware MAC.
- `iprobe leave` removes the profile, so the phone forgets the network.
- `iprobe info` reports the model, iOS version, Wi-Fi MAC, whether the phone
  is supervised, and its battery.
- `iprobe log [--seconds 20]` records the phone's `wifid` log and keeps the
  lines about joining. `PhoneBed.diagnose()` adds it to failure messages. A
  phone that doesn't join retries every 1–15 s, and the log says why:
  `no scan candidate network` means it sees no network matching its
  profile and never tries, so the DUT logs nothing.

The phone can't report whether it associated or what address it got. The
tests read both from the DUT (`hostapd.<ap> get_clients`,
`/tmp/dhcp.leases`) by the phone's MAC, and the DUT pings the phone.

The place is locked per test module (`hold_probe_place`), like the Wi-Fi
probe: the phone has a single network configuration at a time.

## Known results

iPhone 7 (iOS 15.8.8) on an IgniteNet SS-W2-AC2600 (QCA9984, 5 GHz,
channel 36): it joins open and WPA2/WPA3 mixed (`sae-mixed`, PMF on) APs
within about 7 s, gets a lease and answers pings. It doesn't join a
WPA3-only (`sae`) AP: auto-join finds `no scan candidate network`, with
the profile's EncryptionType `WPA3` as well as `Any`. The cause is open.
`test_iphone_joins[*-sae]` is marked xfail (not strict) until it's
understood.

PMF on a mixed AP doesn't prove SAE: a WPA2 client that supports PMF
negotiates it too. Which AKM was used only shows on the DUT with
`hostapd_cli -i <ap> sta <mac>` (package `hostapd-utils`, not in default
images): `000fac-8` is SAE, `000fac-2` PSK.

## Phone

Any iPhone 7 or newer: they all do 802.11ac on 2.4 and 5 GHz and WPA3.
An iPhone 7 runs at most iOS 15.8.x, an iPhone 8 or X iOS 16.7.x. Up to
iOS 16 the phone serves everything used here over plain usbmuxd. iOS 17
and newer need a RemoteXPC tunnel (`pymobiledevice3 remote tunneld`), which
`iprobe` doesn't set up, so a newer phone needs that added first.

Set it up once, with the phone in reach:

1. Update to the latest iOS it gets (iPhone 7: 15.8.x), then erase the phone
   (Settings → General → Transfer or Reset → Erase All Content and
   Settings) and leave it at the first setup screen.
2. On the host, plug it in and create the keybag, then supervise the phone:

   ```shell
   pymobiledevice3 profile create-keybag /etc/iprobe/keybag.pem openwrt-tests
   pymobiledevice3 profile supervise openwrt-tests --keybag /etc/iprobe/keybag.pem
   ```

   `supervise` activates the phone if needed and skips most setup screens.
   Keep the keybag: without it nothing can install profiles silently, and
   a new one means erasing the phone again. Finish setup **without an
   Apple ID**.
3. **No passcode.** Otherwise the phone is locked after every reboot and
   the services `iprobe` uses are unreachable until someone types it in.
4. Settings → Display & Brightness → Auto-Lock: **Never**. A sleeping phone
   drops off Wi-Fi and stops answering pings.
5. Accept "Trust this computer" when the host pairs for the first time
   (`pymobiledevice3 lockdown info`).
6. Remove any other Wi-Fi network the phone knows, so it only ever joins
   the test AP.

Battery: a phone kept at 100% around the clock swells. Turn on Optimized
Battery Charging, and ideally put the phone on a USB hub port that
`uhubctl` can switch, so it only charges between about 30% and 80%. The `iphone_probe` fixture fails below 20%.

Radio: the phone learns its regulatory domain from the cellular network or
from 802.11d in beacons. Without a SIM it may scan 5 GHz passively, which
makes joins slower but still within `JOIN_TIMEOUT` on channel 36.

## Host

Any Linux host in the lab with USB and SSH, normally the lab host itself:

```shell
apt install usbmuxd
uv venv /opt/iprobe
uv pip install --python /opt/iprobe pymobiledevice3
install -m 0755 contrib/iphone-probe/iprobe.py /opt/iprobe/iprobe.py
```

pymobiledevice3 is the `iphone` extra in openwrt-tests' `pyproject.toml`,
not a default dependency: the tests don't import it, only `iprobe` does. In
an openwrt-tests checkout, `uv sync --extra iphone` installs it into its
venv (e.g. to try `iprobe` on a laptop with the phone attached).

`/usr/local/bin/iprobe`:

```sh
#!/bin/sh
exec /opt/iprobe/bin/python -I /opt/iprobe/iprobe.py "$@"
```

`/etc/iprobe/iprobe.json` (the UDID is shown by `pymobiledevice3 usbmux list`):

```json
{"udid": "<UDID>", "keybag": "/etc/iprobe/keybag.pem"}
```

Create an `iprobe` user with the lab's SSH keys that can read the config
and keybag. usbmuxd's socket is open to all users, so no sudo is needed.
Pin the pymobiledevice3 version once it works: `iprobe` uses its async
Python API (`create_using_usbmux`, `MobileConfigService`), which has
changed between releases.

## labgrid

The phone is a place of its own, tagged `role=iphone-probe`. Set
`iphone_probe: true` for the lab in `labnet.yaml`, and the `labgrid_lab`
role creates the place. In the exporter config:

```yaml
labgrid-<lab>-iphone-probe:
  NetworkService:
    address: <host the phone is plugged into>
    port: 22
    username: iprobe
```

## Running the tests

```shell
tests/.venv/bin/pytest -c tests/pyproject.toml tests/tests/test_wifi_iphone.py \
  --lg-env tests/targets/<target>.yaml ...
```

Without a `role=iphone-probe` place, or on QEMU, the module is skipped.
