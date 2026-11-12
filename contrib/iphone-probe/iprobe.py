#!/usr/bin/env python3
"""iprobe: an iPhone on USB as a Wi-Fi client for openwrt-tests.

Runs on the host the phone is plugged into (docs/iphone-probe.md). The
tests call it over SSH through drivers/iphoneprobedriver.py: one JSON
document on stdout, exit 0 (ok), 1 (ran, but the expected thing didn't
happen) or 2 (error).

    iprobe info
    iprobe join --ssid owci-1a2b3c-5g-0 --encryption psk2 --key openwrt4all
    iprobe leave

The phone joins a network through a configuration profile with a Wi-Fi
payload, installed silently: the phone must be supervised with the keybag in
the config (`pymobiledevice3 profile supervise openwrt-tests --keybag <it>`,
once, on an erased phone). Removing the profile
makes the phone forget the network and leave it. The phone can't tell us
reliably whether it associated or which address it got; the tests read that
from the DUT (hostapd, dnsmasq leases) by the phone's MAC, which the profile
pins: MAC randomization ("Private Wi-Fi Address") is off for the network.

Config: /etc/iprobe/iprobe.json (--config or IPROBE_CONFIG override it):

    {"udid": "<40 hex digits>", "keybag": "/etc/iprobe/keybag.pem"}

Needs pymobiledevice3 (async API) and usbmuxd. Up to iOS 16 (iPhone 7:
iOS 15, iPhone 8: iOS 16) the phone serves everything used here over
usbmuxd, no tunnel needed.
"""

import argparse
import asyncio
import json
import os
import plistlib
import re
import subprocess
import sys
import uuid
from pathlib import Path

OK, FAILED, ERROR = 0, 1, 2

DEFAULT_CONFIG = "/etc/iprobe/iprobe.json"
# One profile at a time: joining replaces it, leaving removes it
PROFILE_ID = "org.openwrt.tests.iprobe.wifi"
ORGANIZATION = "openwrt-tests"

# The DUT's encryption (uci wireless encryption) -> the profile's
# EncryptionType. "Any" is what a user gets typing a password in Settings:
# the phone picks WPA2 or WPA3 from the beacon. "WPA2"/"WPA3" pin it.
ENCRYPTION_TYPES = {
    "none": "None",
    "any": "Any",
    "psk2": "WPA2",
    "psk-mixed": "WPA2",
    "sae": "WPA3",
    "sae-mixed": "Any",
}

# wifid lines that explain a join or its absence. The rest of wifid's log
# is noise, and the scan results themselves are redacted (<private>).
WIFID_KEEP = re.compile(
    r"no scan candidate|auto-join failed|Retrying auto association"
    r"|link (up|down)|Disassociate|4 way|JoinDone|Filtered networks"
    r"|Add network|Remove network"
)
# Lines `iprobe log` returns at most
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
    if not config.get("udid"):
        raise ProbeError(f"{path} has no udid")
    return config


async def lockdown_client(config):
    from pymobiledevice3.lockdown import create_using_usbmux

    try:
        return await create_using_usbmux(serial=config["udid"], autopair=False)
    except Exception as e:
        raise ProbeError(
            f"phone {config['udid']} not reachable over usbmuxd "
            f"(plugged in, trusted, unlocked once since boot?): {e}"
        ) from e


def wifi_profile(ssid, encryption, key, hidden):
    """A configuration profile with one com.apple.wifi.managed payload."""
    payload = {
        "PayloadType": "com.apple.wifi.managed",
        "PayloadIdentifier": f"{PROFILE_ID}.payload",
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadVersion": 1,
        "PayloadDisplayName": f"Wi-Fi {ssid}",
        "SSID_STR": ssid,
        "HIDDEN_NETWORK": bool(hidden),
        "AutoJoin": True,
        "EncryptionType": ENCRYPTION_TYPES[encryption],
        # The tests find the phone on the DUT by its hardware MAC
        "DisableAssociationMACRandomization": True,
        "CaptiveBypass": True,
    }
    if encryption != "none":
        payload["Password"] = key
    profile = {
        "PayloadType": "Configuration",
        "PayloadIdentifier": PROFILE_ID,
        "PayloadUUID": str(uuid.uuid4()).upper(),
        "PayloadVersion": 1,
        "PayloadDisplayName": "openwrt-tests Wi-Fi",
        "PayloadOrganization": ORGANIZATION,
        "PayloadRemovalDisallowed": False,
        "PayloadContent": [payload],
    }
    return plistlib.dumps(profile)


async def cmd_info(config, args):
    lockdown = await lockdown_client(config)
    async with lockdown:
        from pymobiledevice3.services.mobile_config import MobileConfigService

        battery = await lockdown.get_value(domain="com.apple.mobile.battery")
        async with MobileConfigService(lockdown=lockdown) as mc:
            cloud = await mc.get_cloud_configuration() or {}
            profiles = await mc.get_profile_list()
        info = {
            "udid": config["udid"],
            "name": lockdown.display_name,
            "hardware_model": lockdown.hardware_model,
            "ios": lockdown.product_version,
            "build": lockdown.product_build_version,
            "wifi_mac": (lockdown.wifi_mac_address or "").lower(),
            "supervised": bool(cloud.get("IsSupervised")),
            "battery_percent": (battery or {}).get("BatteryCurrentCapacity"),
            "charging": (battery or {}).get("BatteryIsCharging"),
            "joined_profile": PROFILE_ID in profiles.get("ProfileMetadata", {}),
        }
    return reply(OK, **info)


async def cmd_join(config, args):
    keybag = Path(config.get("keybag", "/etc/iprobe/keybag.pem"))
    if not keybag.is_file():
        raise ProbeError(
            f"no supervision keybag at {keybag} (pymobiledevice3 profile "
            "create-keybag, then profile supervise --keybag on an erased phone)"
        )
    if args.encryption != "none" and not args.key:
        raise ProbeError(f"--key needed for {args.encryption}")
    profile = wifi_profile(args.ssid, args.encryption, args.key, args.hidden)
    lockdown = await lockdown_client(config)
    async with lockdown:
        from pymobiledevice3.services.mobile_config import MobileConfigService

        mac = (lockdown.wifi_mac_address or "").lower()
        async with MobileConfigService(lockdown=lockdown) as mc:
            # Forget the previous network first, or the phone may stay on it
            await mc.remove_profile(PROFILE_ID)
        # No SetWiFiPowerState: iOS 15 rejects it ("not a valid request
        # type"); Wi-Fi stays on unless someone turns it off on the phone
        async with MobileConfigService(lockdown=lockdown) as mc:
            await mc.install_profile_silent(keybag, profile)
    return reply(
        OK,
        ssid=args.ssid,
        encryption=args.encryption,
        encryption_type=ENCRYPTION_TYPES[args.encryption],
        hidden=bool(args.hidden),
        mac=mac,
    )


async def cmd_leave(config, args):
    lockdown = await lockdown_client(config)
    async with lockdown:
        from pymobiledevice3.services.mobile_config import MobileConfigService

        async with MobileConfigService(lockdown=lockdown) as mc:
            had = PROFILE_ID in (await mc.get_profile_list()).get("ProfileMetadata", {})
            await mc.remove_profile(PROFILE_ID)
    return reply(OK, removed=had)


def cmd_log(config, args):
    """The phone's wifid log for `seconds`, filtered to join decisions. A
    phone that doesn't join retries every 1-15 s, so ~20 s show why."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-m",
            "pymobiledevice3",
            "syslog",
            "live",
            "--process-name",
            "wifid",
            "--udid",
            config["udid"],
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        out, _ = proc.communicate(timeout=args.seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    lines = [line for line in out.splitlines() if WIFID_KEEP.search(line)]
    return reply(OK, seconds=args.seconds, lines=lines[-LOG_LINES:])


COMMANDS = {
    "info": cmd_info,
    "join": cmd_join,
    "leave": cmd_leave,
    "log": cmd_log,
}


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="iprobe", description=__doc__.split("\n")[0])
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("IPROBE_CONFIG", DEFAULT_CONFIG)),
        help=f"JSON config (default: $IPROBE_CONFIG or {DEFAULT_CONFIG})",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("info", help="the phone: model, iOS, Wi-Fi MAC, supervised")
    join = sub.add_parser("join", help="join a network (replaces the previous one)")
    join.add_argument("--ssid", required=True)
    join.add_argument("--encryption", choices=sorted(ENCRYPTION_TYPES), default="any")
    join.add_argument("--key")
    join.add_argument("--hidden", action=argparse.BooleanOptionalAction, default=False)
    sub.add_parser("leave", help="forget the network (removes the profile)")
    log = sub.add_parser("log", help="the phone's Wi-Fi log, join decisions only")
    log.add_argument("--seconds", type=int, default=20)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        config = load_config(args.config)
        result = COMMANDS[args.command](config, args)
        # The pymobiledevice3 commands are coroutines, `log` is not
        return asyncio.run(result) if asyncio.iscoroutine(result) else result
    except ProbeError as e:
        return reply(ERROR, error=str(e))
    except Exception as e:  # noqa: BLE001 (one JSON reply, whatever happened)
        return reply(ERROR, error=f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
