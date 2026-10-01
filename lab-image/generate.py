#!/usr/bin/env python3
"""Generate the per-lab file overlay for an OpenWrt based labgrid host.

Reads ``labnet.yaml`` and ``ansible/files/exporter/<lab>/`` and writes a
directory tree that the OpenWrt ImageBuilder copies into the image (FILES=).
Everything that differs between labs ends up in this overlay; the static
parts live in ``lab-image/files/``.
"""

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

import yaml

# Public key of the CI runner, also authorized by ansible/lab.yml
CI_PUBKEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIP0ZVlD9TmfAXL53Vq7V9WKE3KPomOa1jINyflrPWAlJ"
)

# VLANs every lab gets, matching ansible/files/exporter/netplan.yaml
DEFAULT_VLANS = list(range(101, 125)) + [200]

# udev based resources which need udevd properties that OpenWrt doesn't have
UDEV_ONLY = {
    "USBSDMuxDevice",
    "USBPowerPort",
    "USBSDWireDevice",
    "USBMassStorage",
    "USBNetworkInterface",
}

PDUDAEMON_DEFAULT = {
    "daemon": {
        "hostname": "localhost",
        "port": 16421,
        "logging_level": "INFO",
        "listener": "http",
    },
    "pdus": {"192.168.128.2": {"driver": "ubus"}},
}


def warn(msg):
    print(f"WARNING: {msg}", file=sys.stderr)


def convert_serial(place, res):
    """Map a udev matched USBSerialPort to a RawSerialPort.

    The hotplug script /etc/hotplug.d/tty/50-labgrid-serial creates the
    /dev/labgrid/by-path and /dev/labgrid/by-serial links used here.
    """
    match = dict(res.get("match", {}))
    port = match.pop("@port_number", "0")
    id_path = match.pop("ID_PATH", None) or match.pop("@ID_PATH", None)
    serial = match.pop("ID_SERIAL_SHORT", None) or match.pop("@ID_SERIAL_SHORT", None)
    ifnum = match.pop("ID_USB_INTERFACE_NUM", "00")

    if match:
        raise ValueError(
            f"{place}: USBSerialPort match keys {sorted(match)} are not supported "
            "on OpenWrt, use ID_PATH (+ @port_number) or ID_SERIAL_SHORT"
        )

    if id_path:
        dev = f"/dev/labgrid/by-path/{id_path}-port{port}"
    elif serial:
        dev = f"/dev/labgrid/by-serial/{serial}-if{int(ifnum):02d}-port{port}"
    else:
        raise ValueError(f"{place}: USBSerialPort needs ID_PATH or ID_SERIAL_SHORT")

    new = {"port": dev, "speed": res.get("speed", 115200)}
    if "cls" in res:
        new["cls"] = "RawSerialPort"
    return new


def convert_exporter(exporter):
    """Return the exporter config with udev-only resources rewritten."""
    out = {}
    for place, resources in exporter.items():
        if not isinstance(resources, dict):
            out[place] = resources
            continue

        new = {}
        for name, res in resources.items():
            cls = res.get("cls", name) if isinstance(res, dict) else name
            if cls == "USBSerialPort":
                key = "RawSerialPort" if name == "USBSerialPort" else name
                new[key] = convert_serial(place, res)
            else:
                if cls in UDEV_ONLY:
                    warn(f"{place}: {cls} relies on udev and won't be found on OpenWrt")
                new[name] = res
        out[place] = new
    return out


def collect_vlans(exporter):
    """VLAN IDs referenced by the exporter config plus the default set."""
    vlans = set(DEFAULT_VLANS)
    text = yaml.safe_dump(exporter)
    vlans.update(int(v) for v in re.findall(r"%vlan(\d+)", text))
    vlans.update(int(v) for v in re.findall(r"tftp://192\.168\.(\d+)\.1/", text))
    return sorted(v for v in vlans if 1 <= v <= 4094)


def tftp_dirs(exporter):
    dirs = set()
    for resources in exporter.values():
        if not isinstance(resources, dict):
            continue
        for name, res in resources.items():
            if not isinstance(res, dict):
                continue
            if res.get("cls", name) == "TFTPProvider" and res.get("internal"):
                dirs.add(res["internal"].rstrip("/"))
    return sorted(dirs)


def places(lab, labdef, epoch):
    out = {}
    instances = labdef.get("device_instances", {})
    for device in labdef.get("devices", []):
        for instance in instances.get(device, [device]):
            out[f"{lab}-{instance}"] = {
                "acquired": None,
                "acquired_resources": [],
                "aliases": [],
                "allowed": [],
                "changed": epoch,
                "comment": "",
                "created": epoch,
                "matches": [
                    {
                        "cls": "*",
                        "exporter": "*",
                        "group": f"{lab}-{instance}",
                        "name": None,
                        "rename": None,
                    }
                ],
                "tags": {"device": device},
            }
    return out


def github_keys(users, offline):
    keys = []
    for user in users:
        if offline:
            warn(f"offline: not fetching SSH keys of {user}")
            continue
        url = f"https://github.com/{user}.keys"
        with urllib.request.urlopen(url, timeout=30) as r:
            for line in r.read().decode().splitlines():
                if line.strip():
                    keys.append(f"{line.strip()} {user}")
    return keys


def write(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab", required=True, help="lab name, e.g. labgrid-aparcar")
    parser.add_argument("--labnet", default="labnet.yaml", type=Path)
    parser.add_argument("--exporter-dir", default="ansible/files/exporter", type=Path)
    parser.add_argument(
        "--out", required=True, type=Path, help="overlay output directory"
    )
    parser.add_argument(
        "--trunk", help="interface carrying the DUT VLANs (default eth0)"
    )
    parser.add_argument("--wan", help="uplink interface using DHCP (default eth0)")
    parser.add_argument("--offline", action="store_true", help="don't fetch SSH keys")
    args = parser.parse_args()

    labnet = yaml.safe_load(args.labnet.read_text())
    if args.lab not in labnet["labs"]:
        sys.exit(f"Lab {args.lab} not found in {args.labnet}")
    labdef = labnet["labs"][args.lab]
    image = labdef.get("image", {})

    srcdir = args.exporter_dir / args.lab
    exporter = yaml.safe_load((srcdir / "exporter.yaml").read_text())
    exporter = convert_exporter(exporter)

    pdu_conf = srcdir / "pdudaemon.conf"
    if pdu_conf.exists():
        pdudaemon = json.loads(pdu_conf.read_text())
    else:
        pdudaemon = PDUDAEMON_DEFAULT

    if (srcdir / "netplan.yaml").exists() or (srcdir / "dnsmasq.conf").exists():
        warn(
            f"{srcdir} has a custom netplan/dnsmasq config, only the VLANs are "
            "carried over; set image.trunk/image.wan in labnet.yaml if needed"
        )

    epoch = int(time.time())
    out = args.out
    etc = out / "etc/labgrid"

    write(
        etc / "exporter.yaml",
        f"# Generated from {srcdir}/exporter.yaml by lab-image/generate.py\n"
        + yaml.safe_dump(exporter, sort_keys=False),
    )
    write(
        etc / "places.yaml",
        yaml.safe_dump(places(args.lab, labdef, epoch), sort_keys=False),
    )
    write(etc / "pdudaemon.conf", json.dumps(pdudaemon, indent=4) + "\n")

    vlans = collect_vlans(exporter)
    trunk = args.trunk or image.get("trunk", "eth0")
    wan = args.wan or image.get("wan", "eth0")
    known = {f"vlan{v}" for v in vlans} | {trunk, wan}
    for ifname in sorted(set(re.findall(r"%([\w.-]+)", yaml.safe_dump(exporter)))):
        if ifname not in known:
            warn(f"NetworkService interface {ifname} isn't configured by the image")

    for d in tftp_dirs(exporter):
        if not d.startswith("/srv/tftp/"):
            warn(f"TFTPProvider {d} is outside of the TFTP root /srv/tftp")

    lab_conf = {
        "LAB": args.lab,
        "TRUNK": trunk,
        "WAN": wan,
        "VLANS": " ".join(str(v) for v in vlans),
        "TFTP_DIRS": " ".join(tftp_dirs(exporter)),
    }
    write(etc / "lab.conf", "".join(f"{k}='{v}'\n" for k, v in lab_conf.items()))

    maintainers = labdef.get("maintainers", [])
    access = sorted(set(maintainers + labdef.get("access", [])))
    dev_keys = github_keys(access, args.offline) + [f"{CI_PUBKEY} ci"]
    root_keys = [k for k in dev_keys if k.rsplit(" ", 1)[-1] in maintainers]

    write(
        out / "home/labgrid-dev/.ssh/authorized_keys", "\n".join(dev_keys) + "\n", 0o600
    )
    write(out / "root/.ssh/authorized_keys", "\n".join(root_keys) + "\n", 0o600)

    print(f"Generated overlay for {args.lab} in {out}")
    for k, v in lab_conf.items():
        print(f"  {k}={v}")


if __name__ == "__main__":
    main()
