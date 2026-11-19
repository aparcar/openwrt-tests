#!/usr/bin/env python3
"""aprobe: an Android phone on USB as a Wi-Fi client for openwrt-tests.

Runs on the host the phone is plugged into (docs/android-probe.md). The
tests call it over SSH through drivers/androidprobedriver.py, exactly like
iprobe (contrib/iphone-probe): one JSON document on stdout, exit 0 (ok),
1 (ran, but the expected thing didn't happen) or 2 (error).

    aprobe info
    aprobe join --ssid owci-1a2b3c-5g-0 --encryption psk2 --key openwrt4all
    aprobe leave

It drives the phone with adb and root (Magisk, granted to the shell), no
app: `cmd wifi connect-network` adds and joins a network, `cmd wifi
forget-network` drops it. Both are root-only on user builds. The phone is
a dedicated probe: `leave` forgets every saved network.

The tests find the phone on the DUT by its MAC. join turns MAC
randomization off for the network, so that is the phone's factory MAC,
which `dumpsys wifi` shows to root.

Config: /etc/aprobe/aprobe.json (--config or APROBE_CONFIG override it):

    {"serial": "<adb serial>"}
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

OK, FAILED, ERROR = 0, 1, 2

DEFAULT_CONFIG = "/etc/aprobe/aprobe.json"
ADB_TIMEOUT = 30

# The DUT's encryption (uci wireless encryption) -> connect-network's
# security type. "wpa2" is what a user gets typing a password: on a WPA2/WPA3
# transition AP Android upgrades to SAE by itself. "wpa3" pins SAE.
SECURITY_TYPES = {
    "none": "open",
    "any": "wpa2",
    "psk2": "wpa2",
    "psk-mixed": "wpa2",
    "sae": "wpa3",
    "sae-mixed": "wpa2",
}

# logcat lines that explain a join or its absence
LOG_KEEP = re.compile(
    r"wpa_supplicant|WifiClientModeImpl|ClientModeImpl|WifiNetworkSelector"
    r"|WifiConnectivityManager|SupplicantStaIfaceHal"
)
# Lines `aprobe log` returns at most
LOG_LINES = 80


class ProbeError(Exception):
    pass


def reply(status, **data):
    print(json.dumps(data, sort_keys=True))
    return status


def load_config(path):
    try:
        config = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ProbeError(f"can't read {path}: {e}") from e
    if not config.get("serial"):
        raise ProbeError(f"{path} has no serial")
    return config


def shell_command(argv):
    """argv as the one string adb hands to the phone's sh, run as root: sh
    parses it, then su's sh parses the inner string, so both are quoted
    (SSIDs, keys)."""
    return "su -c " + shlex.quote(shlex.join(argv))


def adb_shell(config, *argv, timeout=ADB_TIMEOUT):
    """Run argv on the phone as root; its stdout."""
    cmd = ["adb", "-s", config["serial"], "shell", shell_command(argv)]
    try:
        proc = subprocess.run(
            cmd, check=False, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as e:
        raise ProbeError("adb not installed") from e
    except subprocess.TimeoutExpired as e:
        raise ProbeError(f"{shlex.join(argv)}: no answer in {timeout}s") from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[-300:]
        raise ProbeError(
            f"{shlex.join(argv)} exited {proc.returncode} (phone {config['serial']} "
            "plugged in, USB debugging allowed for this host, root granted to "
            f"the shell in Magisk?): {detail}"
        )
    return proc.stdout


def parse_network_ids(text):
    """`cmd wifi list-networks` -> the saved networks' ids, once each (a
    WPA2 network is listed twice: wpa2-psk and its wpa3-sae upgrade)."""
    ids = []
    for line in text.splitlines():
        first = line.split(maxsplit=1)[0] if line.strip() else ""
        if first.isdigit():
            ids.append(int(first))
    return list(dict.fromkeys(ids))


def parse_factory_mac(text):
    """`dumpsys wifi` -> the factory MAC, or None"""
    match = re.search(r"wifi_sta_factory_mac_address=([0-9a-fA-F:]{17})", text)
    return match.group(1).lower() if match else None


def factory_mac(config):
    mac = parse_factory_mac(adb_shell(config, "dumpsys", "wifi"))
    if not mac:
        raise ProbeError("no wifi_sta_factory_mac_address in dumpsys wifi")
    return mac


def parse_battery(text):
    """`dumpsys battery` -> (percent, charging)"""
    fields = {
        k.strip(): v.strip()
        for k, _, v in (line.partition(":") for line in text.splitlines())
    }
    level = fields.get("level")
    # BatteryManager.BATTERY_STATUS_CHARGING
    return (
        int(level) if level and level.isdigit() else None,
        fields.get("status") == "2",
    )


def connect_argv(ssid, encryption, key, hidden):
    argv = ["cmd", "wifi", "connect-network", ssid, SECURITY_TYPES[encryption]]
    if encryption != "none":
        argv.append(key)
    # The tests find the phone on the DUT by its factory MAC
    argv += ["-r", "none"]
    if hidden:
        argv.append("-h")
    return argv


def forget_all(config):
    ids = parse_network_ids(adb_shell(config, "cmd", "wifi", "list-networks"))
    for network_id in ids:
        adb_shell(config, "cmd", "wifi", "forget-network", str(network_id))
    return ids


def cmd_info(config, args):
    def prop(name):
        return adb_shell(config, "getprop", name).strip()

    battery, charging = parse_battery(adb_shell(config, "dumpsys", "battery"))
    return reply(
        OK,
        serial=config["serial"],
        model=prop("ro.product.model"),
        android=prop("ro.build.version.release"),
        sdk=prop("ro.build.version.sdk"),
        wifi_mac=factory_mac(config),
        # The MAC wlan0 uses right now: may be randomized while scanning
        wlan0_mac=adb_shell(config, "cat", "/sys/class/net/wlan0/address").strip(),
        battery_percent=battery,
        charging=charging,
        # "Wifi is enabled", "Wifi is connected to "<ssid>"" (or not)
        wifi_status=[
            line
            for line in adb_shell(config, "cmd", "wifi", "status").splitlines()
            if line.startswith("Wifi is")
        ],
    )


def cmd_join(config, args):
    if args.encryption != "none" and not args.key:
        raise ProbeError(f"--key needed for {args.encryption}")
    adb_shell(config, "cmd", "wifi", "set-wifi-enabled", "enabled")
    # Forget the previous network first, or the phone may stay on it
    forget_all(config)
    adb_shell(config, *connect_argv(args.ssid, args.encryption, args.key, args.hidden))
    return reply(
        OK,
        ssid=args.ssid,
        encryption=args.encryption,
        security_type=SECURITY_TYPES[args.encryption],
        hidden=bool(args.hidden),
        mac=factory_mac(config),
    )


def cmd_leave(config, args):
    return reply(OK, removed=bool(forget_all(config)))


def cmd_log(config, args):
    """The phone's Wi-Fi log for `seconds` from now, join decisions only."""
    cmd = ["adb", "-s", config["serial"], "logcat", "-v", "time", "-T", "1"]
    try:
        out = subprocess.run(
            cmd, check=False, capture_output=True, text=True, timeout=args.seconds
        ).stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        out = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
    lines = [line for line in out.splitlines() if LOG_KEEP.search(line)]
    return reply(OK, seconds=args.seconds, lines=lines[-LOG_LINES:])


COMMANDS = {
    "info": cmd_info,
    "join": cmd_join,
    "leave": cmd_leave,
    "log": cmd_log,
}


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="aprobe", description=__doc__.split("\n")[0])
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("APROBE_CONFIG", DEFAULT_CONFIG)),
        help=f"JSON config (default: $APROBE_CONFIG or {DEFAULT_CONFIG})",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("info", help="the phone: model, Android version, Wi-Fi MAC, battery")
    join = sub.add_parser("join", help="join a network (forgets all others)")
    join.add_argument("--ssid", required=True)
    join.add_argument("--encryption", choices=sorted(SECURITY_TYPES), default="any")
    join.add_argument("--key")
    join.add_argument("--hidden", action=argparse.BooleanOptionalAction, default=False)
    sub.add_parser("leave", help="forget every saved network")
    log = sub.add_parser("log", help="the phone's Wi-Fi log, join decisions only")
    log.add_argument("--seconds", type=int, default=20)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return COMMANDS[args.command](load_config(args.config), args)
    except ProbeError as e:
        return reply(ERROR, error=str(e))
    except Exception as e:  # noqa: BLE001 (one JSON reply, whatever happened)
        return reply(ERROR, error=f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
