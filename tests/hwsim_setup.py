# SPDX-License-Identifier: LGPL-2.1-only
"""mac80211_hwsim radios on a QEMU target, set up when a test needs them.

Targets with the `hwsim` feature (qemu_x86-64) boot the official image,
which has no Wi-Fi packages. The `hwsim` fixture installs them with apk from
the release's own feeds (over QEMU's user-mode WAN, so the guest needs
internet), loads mac80211_hwsim and writes /etc/config/wireless, the first
time a test asks for it. Plain runs of the other tests stay as fast as
before.

The result is kept per boot (boot_id): after a reboot or `sysupgrade -n`
the setup runs again. A failure is kept too, so later tests error at once
with the same message instead of retrying the slow apk install each time.

Installing Wi-Fi at run time has pitfalls a firmware with Wi-Fi built in
doesn't have; each step below handles one (see docs/hwsim.md).
"""

import shlex

import pytest

RADIOS = 2
# hwsim itself and ip-full for `ip netns` (the station side runs in a netns)
PACKAGES = ["kmod-mac80211-hwsim", "ip-full"]
# Installed only if the image has no hostapd and wpa_supplicant at all; the
# basic variant is what most boards ship
WPAD = "wpad-basic-mbedtls"
# apk needs the WAN (QEMU user-mode networking) to be up
WAN_TIMEOUT = 90


class HwsimError(Exception):
    """The guest could not be given hwsim radios."""


def _sh(shell, cmd, timeout=60):
    stdout, _stderr, rc = shell.run(cmd, timeout=timeout)
    return "\n".join(stdout), rc


def _missing_packages(shell):
    names = " ".join(shlex.quote(p) for p in PACKAGES)
    out, _ = _sh(shell, f"apk info -e {names} 2>/dev/null")
    installed = set(out.split())
    missing = [p for p in PACKAGES if p not in installed]
    _, rc = _sh(shell, "[ -x /usr/sbin/hostapd ] && [ -x /usr/sbin/wpa_supplicant ]")
    if rc != 0:
        missing.append(WPAD)
    return missing


def _install(shell, packages):
    # wan gets its address from QEMU's user-mode DHCP. A subshell: the serial
    # console runs commands in its login shell, `exit` there would log out.
    _, rc = _sh(
        shell,
        f"(for i in $(seq {WAN_TIMEOUT}); do "
        "ifstatus wan 2>/dev/null | grep -q '\"up\": true' && exit 0; sleep 1; "
        "done; exit 1)",
        timeout=WAN_TIMEOUT + 30,
    )
    if rc != 0:
        raise HwsimError(f"wan not up after {WAN_TIMEOUT} s, cannot install {packages}")
    names = " ".join(shlex.quote(p) for p in packages)
    out, rc = _sh(
        shell, f"apk update >/dev/null 2>&1; apk add {names} 2>&1", timeout=300
    )
    if rc != 0:
        # The tail says which packages and why; the full output is on the console
        tail = "\n".join(out.splitlines()[-8:])
        raise HwsimError(
            f"apk add {names} failed (does the guest reach the feeds?):\n{tail}"
        )


def _start_wpad(shell):
    """netifd sets up wifi-ifaces through the hostapd/wpa_supplicant ubus
    objects of the wpad service. wpad runs as user `network`, and ubusd only
    lets it publish them per /usr/share/acl.d/wpad_acl.json, which ubusd
    reads at boot and on SIGHUP. After an install at run time: SIGHUP ubusd,
    then (re)start wpad, or the objects never appear although wpad runs."""
    _sh(
        shell,
        "killall -HUP ubusd; sleep 1; /etc/init.d/wpad enable; /etc/init.d/wpad restart",
    )
    out, rc = _sh(shell, "ubus -t 30 wait_for hostapd wpa_supplicant 2>&1", timeout=45)
    if rc != 0:
        status, _ = _sh(
            shell,
            "ls /usr/share/acl.d; ubus list; "
            'ubus call service list \'{"name":"wpad"}\'; logread | tail -n 20',
        )
        raise HwsimError(
            f"wpad's ubus objects (hostapd, wpa_supplicant) did not appear:\n{out}\n{status}"
        )


def _start_netifd_wireless(shell):
    """netifd loads its wireless support (wifi-scripts' wireless.uc) once, at
    start: the netifd from boot has none ("Wireless module not found", no
    network.wireless object) and `wifi`/reload_config silently do nothing.
    Restart it once wifi-scripts is there."""
    _, rc = _sh(shell, "ubus list network.wireless >/dev/null 2>&1")
    if rc == 0:
        return
    _sh(shell, "/etc/init.d/network restart")
    out, rc = _sh(shell, "ubus -t 30 wait_for network.wireless 2>&1", timeout=45)
    if rc != 0:
        status, _ = _sh(
            shell,
            "ls /lib/netifd; ubus list 'network*'; logread | grep netifd | tail -n 20",
        )
        raise HwsimError(
            f"netifd has no network.wireless after a restart:\n{out}\n{status}"
        )


def _load_radios(shell):
    out, _ = _sh(shell, "cat /sys/module/mac80211_hwsim/parameters/radios 2>/dev/null")
    if out.strip() != str(RADIOS):
        # the kmod's postinst may have loaded it already
        _sh(shell, "wifi down 2>/dev/null; rmmod mac80211_hwsim 2>/dev/null")
        out, rc = _sh(shell, f"insmod mac80211_hwsim radios={RADIOS} 2>&1")
        if rc != 0:
            raise HwsimError(f"insmod mac80211_hwsim radios={RADIOS} failed:\n{out}")
    # A fresh config for exactly these radios. reload_config only signals
    # changes to configs in its checksum list (written at boot, before
    # wireless existed): without this run, a test's first `uci commit &&
    # reload_config` would be the silent one that only adds the file.
    _sh(shell, "sleep 2; rm -f /etc/config/wireless; wifi config; reload_config")
    out, _ = _sh(
        shell,
        "ls /sys/class/ieee80211 | wc -l; uci -q show wireless | grep -c =wifi-device",
    )
    counts = out.split()
    if counts != [str(RADIOS)] * 2:
        raise HwsimError(f"expected {RADIOS} phys and wifi-devices, got {counts}")


def prepare(shell):
    """Packages, wpad, netifd's wireless support, radios and config."""
    missing = _missing_packages(shell)
    if missing:
        _install(shell, missing)
    _start_wpad(shell)
    _start_netifd_wireless(shell)
    _load_radios(shell)


# boot_id -> None (ready) or the error message of the failed setup
_state = {}


@pytest.fixture
def hwsim(shell_command):
    """hwsim radios on the DUT (once per boot); errors the test if the setup
    failed. Returns the number of radios."""
    boot_id = "".join(shell_command.run_check("cat /proc/sys/kernel/random/boot_id"))
    if boot_id not in _state:
        try:
            prepare(shell_command)
            _state[boot_id] = None
        # Any failure, also labgrid's timeouts: they must not abort the session
        except Exception as e:  # noqa: BLE001
            _state[boot_id] = f"{type(e).__name__}: {e}"
    if _state[boot_id] is not None:
        raise HwsimError(f"hwsim setup failed (once per boot): {_state[boot_id]}")
    return RADIOS
