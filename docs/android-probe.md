# Android Wi-Fi probe (aprobe)

An Android phone joins the DUT's access point as a real Wi-Fi client, to
check that Android's Wi-Fi stack works with OpenWrt: WPA2, WPA3 and the
mixed modes, PMF, hidden SSIDs, and reconnecting after a Wi-Fi restart. It
complements the ath9k Wi-Fi probe (`docs/openwrt-probe.md`) with one real
client.

- On the host the phone is plugged into: `contrib/android-probe/aprobe.py`,
  a CLI that prints one JSON reply and exits 0 (ok), 1 or 2 (error).
- In openwrt-tests: `drivers/androidprobedriver.py` runs `aprobe` over SSH,
  and `tests/android_probe_fixtures.py` provides `android_probe` and
  `PhoneBed`.
- Tests: `tests/test_wifi_android.py`.

## How it works

adb, no app, but root: on user builds `cmd wifi connect-network` and
`forget-network` refuse the adb shell (`Uid 2000 does not have access`), so
`aprobe` runs every phone command as `su -c` (Magisk). Root also shows the
factory MAC (`dumpsys wifi`), so the config only names the phone.

- `aprobe join --ssid S --encryption E --key K [--hidden]` turns Wi-Fi on,
  forgets every saved network and runs `cmd wifi connect-network S
  open|wpa2|wpa3 K -r none [-h]`. `-r none` turns MAC randomization off for
  the network, so the DUT sees the phone's factory MAC.
- `aprobe leave` forgets every saved network. The phone is a dedicated
  probe: don't keep networks on it you need.
- `aprobe info` reports the model, Android version, the factory Wi-Fi MAC
  and the one wlan0 has right now, the battery and `cmd wifi status`.
- `aprobe log [--seconds 20]` records logcat and keeps the Wi-Fi lines
  (wpa_supplicant, ClientModeImpl, WifiNetworkSelector, ...).

Encryption mapping: `none` → open, `psk2`/`psk-mixed`/`sae-mixed`/`any` →
wpa2 (on a WPA2/WPA3 transition AP Android upgrades to SAE by itself, as
for a user typing the password), `sae` → wpa3.

## Phone

Android 12 or newer, with 5 GHz and WPA3 (most phones from 2020 on), and
an unlockable bootloader for root. Tried with a Pixel 5 (Android 14,
Magisk 31.1).

Set it up once:

1. Settings → About phone → tap Build number 7 times; Developer options →
   USB debugging on.
2. Developer options → Stay awake on (screen stays on while charging): a
   sleeping phone may drop Wi-Fi and stop answering pings.
3. No screen lock, no Google account needed. Remove saved Wi-Fi networks.
4. Plug it into the host, run `adb devices` and accept the host's key on
   the phone ("Always allow from this computer").
5. Root it with Magisk: unlock the bootloader (`fastboot flashing unlock`,
   erases the phone), take `boot.img` from the factory image of exactly
   the build the phone runs (`getprop ro.build.fingerprint`), patch it in
   the Magisk app (Install → Select and Patch a File), and `fastboot flash
   boot magisk_patched-*.img`.
6. Magisk → Settings → Superuser Access: Apps and ADB, Automatic Response:
   Grant. `adb shell su -c id` must print `uid=0(root)`.

Battery: a phone at 100% around the clock swells. Use the phone's own
battery protection (charge limit at 80%) if it has one, or a USB hub port
`uhubctl` can switch. The `android_probe` fixture fails below 20%.

## Host

The phone hangs off the lab host. Give the lab its serial (`adb devices`)
in `labs/labgrid-<lab>/lab.yaml`:

```yaml
android_probe:
  serial: 11201FDD40021Q
```

and deploy the lab (`uv run ansible-playbook lab.yml --limit labgrid-<lab>`,
or `--tags android-probe,export` for this alone). The `labgrid_lab` role
installs `adb` (its udev rules give `plugdev` the phone), the `aprobe` user
(in `plugdev`), `/usr/local/bin/aprobe` and `/etc/aprobe/aprobe.json`, and
creates the place. Debian lab hosts only: `labgrid_lab_openwrt` doesn't
set the probe up.

Who may log in as `aprobe`: the lab's `maintainers` and `access` with their
GitHub keys, as for `labgrid-dev` (an Ansible-managed block of
`~aprobe/.ssh/authorized_keys`), and the agent's key (the `labgrid_agent`
role). That login runs nothing but `aprobe`: sshd forces
`/usr/local/sbin/probe-ssh aprobe` (`contrib/probe-ssh/probe_ssh.py`) on it,
which runs `aprobe <args>` or labgrid's keepalive `cat`, without a shell,
and refuses everything else; no terminal, no forwarding. The playbook
checks with `sshd -T` that only `aprobe` gets the forced command.

Once, with the phone in front of you: adb keeps its key in `~aprobe/.android/`,
so the phone must allow it. `-H`: without it sudo may keep your `HOME`, and
adb would use your key instead.

```shell
sudo -H -u aprobe adb devices     # accept "Allow USB debugging?", always allow
sudo -H -u aprobe aprobe info
```

One adb server serves the host (port 5037), started by whoever runs adb
first, with that user's key. Run adb only as `aprobe` there.

## labgrid

The phone is a place of its own, tagged `role=android-probe`. In the
exporter config (`labs/labgrid-<lab>/exporter.yaml`):

```yaml
labgrid-<lab>-android-probe:
  NetworkService:
    address: localhost
    port: 22
    username: aprobe
```

`localhost`: the exporter's own sshd. The agent trusts its host keys under
that name.

## Running the tests

```shell
tests/.venv/bin/pytest -c tests/pyproject.toml tests/tests/test_wifi_android.py \
  --lg-env tests/targets/<target>.yaml ...
```

Without a `role=android-probe` place, or on QEMU, the module is skipped.
`contrib/android-probe/test_aprobe.py` checks aprobe's parsers without a
phone.

## Known results

Pixel 5 (Android 14) on an IgniteNet SS-W2-AC2600, 2.4 and 5 GHz: it joins
open, WPA2, WPA/WPA2 mixed and WPA2/WPA3 mixed APs (PMF on) in 2.5–5 s.
WPA3-only (`sae`) joins in 2.5–5 s too, with OpenWrt's default SAE-H2E
(`sae_pwe=2`) and without (`sae_pwe=0`, which openwrt commit 70ba7512
made configurable for Android phones that trip over H2E). The phone must be
told WPA3 there: a WPA2 network (what `aprobe join --encryption any`
creates) never matches a WPA3-only AP; it joined at most once, after
minutes. `PhoneBed` passes the AP's encryption.
