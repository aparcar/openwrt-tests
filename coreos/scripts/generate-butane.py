#!/usr/bin/env python3
"""
Generate Butane configuration from simple lab config YAML.

Usage:
    ./generate-butane.py lab-config.yaml > labnode.bu
    ./generate-butane.py lab-config.yaml | butane --strict > labnode.ign
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

# Coordinator public key (always included)
COORDINATOR_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIP0ZVlD9TmfAXL53Vq7V9WKE3KPomOa1jINyflrPWAlJ coordinator"


def generate_exporter_yaml(config: dict) -> str:
    """Generate labgrid exporter.yaml content."""
    devices = config.get("devices", [])
    if not devices:
        return "# No devices configured\n"

    lines = ["# Auto-generated labgrid exporter configuration", ""]

    for device in devices:
        name = device["name"]
        lines.append(f"{name}:")

        # Serial port
        if "serial" in device:
            serial = device["serial"]
            lines.append("  USBSerialPort:")
            lines.append("    match:")
            lines.append(f"      ID_PATH: {serial['id_path']}")
            if "speed" in serial:
                lines.append(f"    speed: {serial['speed']}")

        # Network service
        if "network" in device:
            net = device["network"]
            vlan = net.get("vlan", 101)
            vlan_ip = f"192.168.{vlan}.1"
            lines.append("  NetworkService:")
            lines.append(f"    address: {vlan_ip}%vlan{vlan}")
            lines.append("    username: root")

        # Power control
        if "power" in device:
            power = device["power"]
            lines.append("  PDUDaemonPort:")
            lines.append("    host: localhost:16421")
            lines.append(f"    pdu: {power['pdu']}")
            lines.append(f"    index: {power['outlet']}")

        # TFTP provider
        lines.append("  TFTPProvider:")
        lines.append(f"    internal: /srv/tftp/{name}/")
        lines.append(f"    external: {name}/")
        lines.append("")

    return "\n".join(lines)


def generate_pdudaemon_conf(config: dict) -> str:
    """Generate pdudaemon.conf content."""
    pdus = config.get("pdus", [])

    pdu_config = {
        "daemon": {"hostname": "0.0.0.0", "port": 16421, "logging_level": "INFO"},
        "pdus": {},
    }

    for pdu in pdus:
        addr = pdu["address"]
        driver = pdu.get("driver", "ubus")
        pdu_entry = {"driver": driver}

        if driver == "netio4":
            pdu_entry["username"] = pdu.get("username", "netio")
            pdu_entry["password"] = pdu.get("password", "netio")
            pdu_entry["telnetport"] = pdu.get("telnetport", 23)

        pdu_config["pdus"][addr] = pdu_entry

    return json.dumps(pdu_config, indent=2)


def generate_dnsmasq_vlan_conf(vlan: dict) -> str:
    """Generate dnsmasq VLAN config."""
    vlan_id = vlan["id"]
    lines = [
        f"# VLAN {vlan_id} DHCP configuration",
        f"interface=vlan{vlan_id}",
        f"dhcp-range=set:vlan{vlan_id},{vlan['dhcp_start']},{vlan['dhcp_end']},24h",
    ]

    # Extract gateway from address
    addr = vlan.get("address", f"192.168.{vlan_id}.1/24")
    gateway = addr.split("/")[0]
    lines.append(f"dhcp-option=tag:vlan{vlan_id},option:router,{gateway}")

    return "\n".join(lines)


def generate_butane(config: dict) -> dict:
    """Generate complete Butane configuration."""
    lab = config.get("lab", {})
    hostname = lab.get("hostname", "labgrid-node")
    registry = config.get("registry", "ghcr.io/openwrt/openwrt-tests")
    updates = config.get("updates", {})

    # SSH keys
    ssh_keys = [COORDINATOR_KEY] + config.get("ssh_keys", [])

    # Build Butane structure
    butane = {
        "variant": "fcos",
        "version": "1.5.0",
        "passwd": {
            "users": [
                {
                    "name": "labgrid",
                    "groups": ["wheel", "sudo", "dialout", "plugdev"],
                    "ssh_authorized_keys": ssh_keys,
                    "shell": "/bin/bash",
                }
            ]
        },
        "storage": {"directories": [], "files": []},
        "systemd": {"units": []},
    }

    # Directories
    dirs = [
        "/etc/labgrid",
        "/etc/pdudaemon",
        "/etc/dnsmasq.d",
        "/etc/containers/systemd",
    ]
    for d in dirs:
        butane["storage"]["directories"].append({"path": d, "mode": 0o755})

    # Special directories with ownership
    butane["storage"]["directories"].extend(
        [
            {
                "path": "/srv/tftp",
                "mode": 0o755,
                "user": {"name": "labgrid"},
                "group": {"name": "labgrid"},
            },
            {
                "path": "/var/cache/labgrid",
                "mode": 0o755,
                "user": {"name": "labgrid"},
                "group": {"name": "labgrid"},
            },
        ]
    )

    # Device TFTP directories
    for device in config.get("devices", []):
        butane["storage"]["directories"].append(
            {
                "path": f"/srv/tftp/{device['name']}",
                "mode": 0o755,
                "user": {"name": "labgrid"},
                "group": {"name": "labgrid"},
            }
        )

    files = butane["storage"]["files"]

    # Hostname
    files.append(
        {"path": "/etc/hostname", "mode": 0o644, "contents": {"inline": hostname}}
    )

    # Quadlet: labgrid-coordinator
    files.append(
        {
            "path": "/etc/containers/systemd/labgrid-coordinator.container",
            "mode": 0o644,
            "contents": {
                "inline": f"""[Unit]
Description=Labgrid Coordinator
After=network-online.target
Wants=network-online.target

[Container]
Image={registry}/labgrid:latest
ContainerName=labgrid-coordinator
Environment=LABGRID_MODE=coordinator
Environment=LABGRID_COORDINATOR_LISTEN=::
PublishPort=20408:20408
Volume=/etc/labgrid:/etc/labgrid:ro
AutoUpdate=registry
Label=io.containers.autoupdate=registry

[Service]
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
"""
            },
        }
    )

    # Quadlet: labgrid-exporter
    files.append(
        {
            "path": "/etc/containers/systemd/labgrid-exporter.container",
            "mode": 0o644,
            "contents": {
                "inline": f"""[Unit]
Description=Labgrid Exporter
After=network-online.target labgrid-coordinator.service
Wants=network-online.target

[Container]
Image={registry}/labgrid:latest
ContainerName=labgrid-exporter
Environment=LABGRID_MODE=exporter
Environment=LABGRID_CONFIG=/etc/labgrid/exporter.yaml
Environment=LG_CROSSBAR=ws://localhost:20408/ws
Network=host
Volume=/etc/labgrid:/etc/labgrid:ro
Volume=/srv/tftp:/srv/tftp:rw
Volume=/var/cache/labgrid:/var/cache/labgrid:rw
AddDevice=/dev/ttyUSB0
AddDevice=/dev/ttyUSB1
AddDevice=/dev/ttyUSB2
AddDevice=/dev/ttyUSB3
AddDevice=/dev/ttyACM0
AddDevice=/dev/ttyACM1
GroupAdd=dialout
GroupAdd=plugdev
SecurityLabelDisable=true
AutoUpdate=registry
Label=io.containers.autoupdate=registry

[Service]
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
"""
            },
        }
    )

    # Quadlet: pdudaemon
    files.append(
        {
            "path": "/etc/containers/systemd/pdudaemon.container",
            "mode": 0o644,
            "contents": {
                "inline": f"""[Unit]
Description=PDU Daemon
After=network-online.target
Wants=network-online.target

[Container]
Image={registry}/pdudaemon:latest
ContainerName=pdudaemon
Network=host
Volume=/etc/pdudaemon:/etc/pdudaemon:ro
AutoUpdate=registry
Label=io.containers.autoupdate=registry

[Service]
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
"""
            },
        }
    )

    # Quadlet: dnsmasq
    files.append(
        {
            "path": "/etc/containers/systemd/dnsmasq.container",
            "mode": 0o644,
            "contents": {
                "inline": f"""[Unit]
Description=Dnsmasq DHCP/TFTP
After=network-online.target
Wants=network-online.target

[Container]
Image={registry}/dnsmasq:latest
ContainerName=dnsmasq
Network=host
AddCapability=NET_ADMIN
AddCapability=NET_RAW
AddCapability=NET_BIND_SERVICE
Volume=/etc/dnsmasq.d:/etc/dnsmasq.d:ro
Volume=/etc/dnsmasq.conf:/etc/dnsmasq.conf:ro
Volume=/srv/tftp:/srv/tftp:ro
AutoUpdate=registry
Label=io.containers.autoupdate=registry

[Service]
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
            },
        }
    )

    # Exporter configuration
    files.append(
        {
            "path": "/etc/labgrid/exporter.yaml",
            "mode": 0o644,
            "contents": {"inline": generate_exporter_yaml(config)},
        }
    )

    # PDUDaemon configuration
    files.append(
        {
            "path": "/etc/pdudaemon/pdudaemon.conf",
            "mode": 0o644,
            "contents": {"inline": generate_pdudaemon_conf(config)},
        }
    )

    # Dnsmasq main config
    files.append(
        {
            "path": "/etc/dnsmasq.conf",
            "mode": 0o644,
            "contents": {
                "inline": """port=0
enable-tftp
tftp-root=/srv/tftp
log-dhcp
conf-dir=/etc/dnsmasq.d/,*.conf
"""
            },
        }
    )

    # VLAN dnsmasq configs
    for vlan in config.get("network", {}).get("vlans", []):
        files.append(
            {
                "path": f"/etc/dnsmasq.d/vlan{vlan['id']}.conf",
                "mode": 0o644,
                "contents": {"inline": generate_dnsmasq_vlan_conf(vlan)},
            }
        )

    # IP forwarding
    files.append(
        {
            "path": "/etc/sysctl.d/99-ip-forward.conf",
            "mode": 0o644,
            "contents": {
                "inline": "net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\n"
            },
        }
    )

    # USB-SD-Mux udev rules
    files.append(
        {
            "path": "/etc/udev/rules.d/99-usbsdmux.rules",
            "mode": 0o644,
            "contents": {
                "inline": """SUBSYSTEM=="scsi_generic", KERNEL=="sg[0-9]*", ATTRS{manufacturer}=="Linux Automation GmbH", ATTRS{product}=="usb-sd-mux*", SYMLINK+="usb-sd-mux/id-$attr{serial}", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="scsi_generic", KERNEL=="sg[0-9]*", ATTRS{manufacturer}=="Pengutronix", ATTRS{product}=="usb-sd-mux*", SYMLINK+="usb-sd-mux/id-$attr{serial}", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="block", KERNEL=="sd[a-z]", ATTRS{manufacturer}=="Linux Automation GmbH", GROUP="plugdev"
SUBSYSTEM=="block", KERNEL=="sd[a-z]", ATTRS{manufacturer}=="Pengutronix", GROUP="plugdev"
"""
            },
        }
    )

    # labgrid-bound-connect script
    files.append(
        {
            "path": "/usr/local/sbin/labgrid-bound-connect",
            "mode": 0o755,
            "contents": {
                "inline": """#!/bin/bash
set -e
[ $# -lt 3 ] && { echo "Usage: $0 <interface> <host> <port>"; exit 1; }
INTERFACE="$1"; HOST="$2"; PORT="$3"
ip link show "$INTERFACE" &>/dev/null || { echo "Error: Interface $INTERFACE not found"; exit 1; }
[[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || { echo "Error: Invalid port"; exit 1; }
exec socat - "TCP:${HOST}:${PORT},bind-dev=${INTERFACE},connect-timeout=10,keepalive"
"""
            },
        }
    )

    # Sudoers
    files.append(
        {
            "path": "/etc/sudoers.d/labgrid-bound-connect",
            "mode": 0o440,
            "contents": {
                "inline": "ALL ALL = NOPASSWD: /usr/local/sbin/labgrid-bound-connect\n"
            },
        }
    )

    # Zincati update config
    os_update = updates.get("os", {})
    update_day = os_update.get("day", "Sun")
    update_time = os_update.get("time", "03:00")
    files.append(
        {
            "path": "/etc/zincati/config.d/55-updates-strategy.toml",
            "mode": 0o644,
            "contents": {
                "inline": f"""[updates]
strategy = "periodic"

[updates.periodic]
time_zone = "UTC"

[[updates.periodic.window]]
days = [ "{update_day}" ]
start_time = "{update_time}"
length_minutes = 120
"""
            },
        }
    )

    # Container auto-update timer
    container_time = updates.get("containers", {}).get("time", "04:00")
    files.append(
        {
            "path": "/etc/systemd/system/podman-auto-update.timer",
            "mode": 0o644,
            "contents": {
                "inline": f"""[Unit]
Description=Podman auto-update timer

[Timer]
OnCalendar=*-*-* {container_time}:00
RandomizedDelaySec=900
Persistent=true

[Install]
WantedBy=timers.target
"""
            },
        }
    )

    # Systemd units
    units = butane["systemd"]["units"]

    units.append({"name": "podman-auto-update.timer", "enabled": True})
    units.append({"name": "zincati.service", "enabled": True})

    # Cache cleanup
    units.append(
        {
            "name": "labgrid-cache-cleanup.timer",
            "enabled": True,
            "contents": """[Unit]
Description=Labgrid cache cleanup timer

[Timer]
OnCalendar=daily
RandomizedDelaySec=3600
Persistent=true

[Install]
WantedBy=timers.target
""",
        }
    )

    units.append(
        {
            "name": "labgrid-cache-cleanup.service",
            "contents": """[Unit]
Description=Clean old labgrid cache files

[Service]
Type=oneshot
ExecStart=/usr/bin/find /var/cache/labgrid -type f -mtime +7 -delete
""",
        }
    )

    # Pull images on first boot
    units.append(
        {
            "name": "pull-labgrid-images.service",
            "enabled": True,
            "contents": f"""[Unit]
Description=Pull labgrid container images
After=network-online.target
Wants=network-online.target
ConditionPathExists=!/var/lib/labgrid-images-pulled

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/podman pull {registry}/labgrid:latest
ExecStart=/usr/bin/podman pull {registry}/pdudaemon:latest
ExecStart=/usr/bin/podman pull {registry}/dnsmasq:latest
ExecStartPost=/usr/bin/touch /var/lib/labgrid-images-pulled

[Install]
WantedBy=multi-user.target
""",
        }
    )

    return butane


def main():
    parser = argparse.ArgumentParser(
        description="Generate Butane config from lab configuration"
    )
    parser.add_argument("config", help="Lab configuration YAML file")
    parser.add_argument(
        "-o", "--output", help="Output file (default: stdout)", default="-"
    )
    args = parser.parse_args()

    # Load config
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Generate Butane
    butane = generate_butane(config)

    # Output
    output = yaml.dump(butane, default_flow_style=False, sort_keys=False)

    if args.output == "-":
        print(output)
    else:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Generated: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
