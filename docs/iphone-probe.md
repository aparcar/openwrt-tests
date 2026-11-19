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
2. Deploy the host first (see Host below), then plug the phone in, create
   the keybag and supervise the phone, as root on the host:

   ```shell
   /opt/iprobe/bin/python -m pymobiledevice3 profile create-keybag /etc/iprobe/keybag.pem openwrt-tests
   /opt/iprobe/bin/python -m pymobiledevice3 profile supervise openwrt-tests --keybag /etc/iprobe/keybag.pem
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
   (`sudo -u iprobe iprobe info`).
6. Remove any other Wi-Fi network the phone knows, so it only ever joins
   the test AP.

Battery: a phone kept at 100% around the clock swells. Turn on Optimized
Battery Charging, and ideally put the phone on a USB hub port that
`uhubctl` can switch, so it only charges between about 30% and 80%. The `iphone_probe` fixture fails below 20%.

Radio: the phone learns its regulatory domain from the cellular network or
from 802.11d in beacons. Without a SIM it may scan 5 GHz passively, which
makes joins slower but still within `JOIN_TIMEOUT` on channel 36.

## Host

The phone hangs off the lab host. Give the lab its UDID
(`pymobiledevice3 usbmux list`, e.g. `uv run --extra iphone pymobiledevice3
usbmux list` in a checkout on a laptop) in `labnet.yaml`:

```yaml
labs:
  labgrid-<lab>:
    iphone_probe:
      udid: <UDID>
```

and deploy the lab (`uv run ansible-playbook lab.yml --limit labgrid-<lab>`,
or `--tags iphone-probe,export` for this alone). The `labgrid_lab` role
installs `usbmuxd`, pymobiledevice3 (pinned,
`iphone_probe_pymobiledevice3_version`: `iprobe` uses its async API, which
changes between releases) in `/opt/iprobe`, `/usr/local/bin/iprobe`,
`/etc/iprobe/iprobe.json`, the `iprobe` user, and creates the place.
`/etc/iprobe` and the keybag are readable by `iprobe` only (and root): the
keybag lets whoever has it install profiles on the phone. usbmuxd's socket
is open to all users, so `iprobe` needs no group. Debian lab hosts only:
`labgrid_lab_openwrt` doesn't set the probe up.

pymobiledevice3 is also the `iphone` extra in openwrt-tests'
`pyproject.toml`, not a default dependency: the tests don't import it, only
`iprobe` does. `uv sync --extra iphone` installs it into a checkout's venv
(e.g. to try `iprobe` on a laptop with the phone attached).

Who may log in as `iprobe`: the keys that log in as `labgrid-dev` (copied on
every run; a key removed there must be removed from
`~iprobe/.ssh/authorized_keys` by hand) and the agent's key (the
`labgrid_agent` role). That login runs nothing but `iprobe`: sshd forces
`/usr/local/sbin/probe-ssh iprobe` (`contrib/probe-ssh/probe_ssh.py`) on it,
which runs `iprobe <args>` or labgrid's keepalive `cat`, without a shell,
and refuses everything else; no terminal, no forwarding. The playbook
checks with `sshd -T` that only `iprobe` gets the forced command.

## labgrid

The phone is a place of its own, tagged `role=iphone-probe`. In the
exporter config (`ansible/files/exporter/labgrid-<lab>/exporter.yaml`):

```yaml
labgrid-<lab>-iphone-probe:
  NetworkService:
    address: localhost
    port: 22
    username: iprobe
```

`localhost`: the exporter's own sshd. The agent trusts its host keys under
that name.

## Running the tests

```shell
tests/.venv/bin/pytest -c tests/pyproject.toml tests/tests/test_wifi_iphone.py \
  --lg-env tests/targets/<target>.yaml ...
```

Without a `role=iphone-probe` place, or on QEMU, the module is skipped.
`contrib/probe-ssh/test_probe_ssh.py` checks the login's command filter.
