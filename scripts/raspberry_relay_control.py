#!/usr/bin/env python3
"""
Raspberry Pi GPIO Relay / PDU Control Script (labgrid integration).

Drives the RPi 5 GPIO lines via the `pinctrl` tool (BCM numbering), one pin
per physical outlet/port. Port numbers are 1-based and map to BCM pins via
PORT_TO_BCM below.

This mirrors the interface of arduino_relay_control.py: a CLI with
`on`, `off`, `toggle`, `status`, `all-on` and `all-off` subcommands and
stable exit codes, so it can be called directly or via pdudaemon's
`localcmdline` driver, e.g.:

    raspberry_relay_control.py on  %s   # %s = 1-based port
    raspberry_relay_control.py off %s

Usage Examples:
  raspberry_relay_control.py on 1 2 3      # Turn ON ports 1,2,3
  raspberry_relay_control.py off 4 5        # Turn OFF ports 4 and 5
  raspberry_relay_control.py toggle 1       # Toggle port 1
  raspberry_relay_control.py status         # Show status of all ports
  raspberry_relay_control.py all-on         # Turn ON all ports
  raspberry_relay_control.py all-off        # Turn OFF all ports

Exit Codes:
  0 - Success
  1 - Could not run pinctrl (command missing / permission denied)
  2 - Command execution error (a pinctrl call failed)
  3 - Invalid arguments
"""

import argparse
import logging
import shutil
import subprocess
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("raspberry_relay_control")

# 1-based port -> BCM GPIO pin. Update this table to match your wiring.
PORT_TO_BCM = {
    1: 4,
    2: 2,
    3: 24,
    4: 3,
    5: 9,
    6: 23,
    7: 17,
    8: 10,
    9: 27,
}

PORT_COUNT = max(PORT_TO_BCM)


def _pinctrl() -> str:
    """Return the path to the pinctrl binary or raise if missing."""
    path = shutil.which("pinctrl")
    if not path:
        raise FileNotFoundError(
            "pinctrl not found on PATH. Install raspi-gpio (pinctrl) and "
            "ensure the user running this script can access /dev/gpiomem."
        )
    return path


def _run_pinctrl(args: list) -> int:
    """Run a pinctrl command; return its exit code."""
    cmd = [_pinctrl()] + args
    logger.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(
            "pinctrl failed (rc=%d): %s",
            result.returncode,
            (result.stderr or result.stdout).strip(),
        )
    return result.returncode


def set_port(port: int, on: bool) -> int:
    """Set a single port on (True) or off (False). Returns exit code."""
    bcm = PORT_TO_BCM.get(port)
    if bcm is None:
        logger.error("Invalid port %s (valid: 1-%d)", port, PORT_COUNT)
        return 3
    level = "dh" if on else "dl"
    return _run_pinctrl(["set", str(bcm), "op", level])


def set_ports(ports, on: bool) -> int:
    """Set multiple ports. Returns 0 only if all succeeded."""
    rc = 0
    for port in ports:
        r = set_port(port, on)
        if r != 0:
            rc = r
    return rc


def toggle_port(port: int) -> int:
    bcm = PORT_TO_BCM.get(port)
    if bcm is None:
        logger.error("Invalid port %s (valid: 1-%d)", port, PORT_COUNT)
        return 3
    # Read current level, then flip it.
    cur = _run_pinctrl_read(bcm)
    new_on = not cur
    return set_port(port, new_on)


def _run_pinctrl_read(bcm: int) -> bool:
    """Return True if the pin is currently driven high, else False."""
    cmd = [_pinctrl(), "get", str(bcm)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    # pinctrl get output looks like: "GPIO 2: level=1 fsel=5 func=OUT"
    # or "2: 1". Treat level=1 / a '1' as high.
    out = (result.stdout or result.stderr).lower()
    if "level=1" in out:
        return True
    if "level=0" in out:
        return False
    # Fallback: last whitespace-separated token that is 0/1
    for tok in reversed(out.replace(":", " ").split()):
        if tok in ("1", "hi", "high"):
            return True
        if tok in ("0", "lo", "low"):
            return False
    return False


def get_status() -> dict:
    """Return a dict with raw response and per-port on/off state."""
    status = {"ports": {}, "raw": []}
    for port in sorted(PORT_TO_BCM):
        bcm = PORT_TO_BCM[port]
        on = _run_pinctrl_read(bcm)
        status["ports"][port] = on
        status["raw"].append(f"{port}:{'ON' if on else 'OFF'}(gpio{bcm})")
    return status


def create_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Raspberry Pi GPIO port/relay control for labgrid",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    sub = parser.add_subparsers(dest="action", help="Available actions")

    on_p = sub.add_parser("on", help="Turn ON one or more ports")
    on_p.add_argument("ports", nargs="+", type=int, help="Port numbers (1-%d)" % PORT_COUNT)

    off_p = sub.add_parser("off", help="Turn OFF one or more ports")
    off_p.add_argument("ports", nargs="+", type=int, help="Port numbers (1-%d)" % PORT_COUNT)

    tog_p = sub.add_parser("toggle", help="Toggle one or more ports")
    tog_p.add_argument("ports", nargs="+", type=int, help="Port numbers (1-%d)" % PORT_COUNT)

    sub.add_parser("status", help="Show status of all ports")
    sub.add_parser("all-on", help="Turn ON all ports")
    sub.add_parser("all-off", help="Turn OFF all ports")
    return parser


def main() -> int:
    parser = create_argument_parser()
    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.action:
        parser.print_help()
        return 3

    try:
        if args.action == "on":
            return set_ports(args.ports, True)
        if args.action == "off":
            return set_ports(args.ports, False)
        if args.action == "toggle":
            rc = 0
            for port in args.ports:
                r = toggle_port(port)
                if r != 0:
                    rc = r
            return rc
        if args.action == "status":
            st = get_status()
            print("STATUS " + " ".join(st["raw"]))
            return 0
        if args.action == "all-on":
            return set_ports(sorted(PORT_TO_BCM), True)
        if args.action == "all-off":
            return set_ports(sorted(PORT_TO_BCM), False)
    except FileNotFoundError as e:
        logger.error("%s", e)
        return 1
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        return 2

    parser.print_help()
    return 3


if __name__ == "__main__":
    sys.exit(main())
