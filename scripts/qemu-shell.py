#!/usr/bin/env python3
#
# qemu-shell.py — boot a QEMU target via labgrid and drop into its serial
# console, like `labgrid-client console` does for real hardware.
#
# Usage:
#   uv run scripts/qemu-shell.py --lg-env targets/qemu_x86-64.yaml \
#       --firmware /path/to/openwrt-x86-64-generic-squashfs-combined.img
#
# Press Ctrl-] to leave the console; QEMU is shut down on exit.
#
# Environment:
#   LG_QEMU_BIN  Override the QEMU binary from the target's tools.qemu_bin.

import argparse
import logging
import os
import select
import sys
import termios
import tty

import labgrid.logging

from labgrid import Environment

ESCAPE = b"\x1d"  # Ctrl-]


def interact(qemu):
    stdin = sys.stdin.fileno()
    old = termios.tcgetattr(stdin)
    tty.setraw(stdin)
    try:
        # trigger a fresh prompt, the one printed during boot was consumed
        qemu._write(b"\n")
        while True:
            ready, _, _ = select.select([qemu._clientsocket, stdin], [], [])
            if qemu._clientsocket in ready:
                data = qemu._read(max_size=4096, timeout=0)
                if not data:
                    break
                os.write(sys.stdout.fileno(), data)
            if stdin in ready:
                data = os.read(stdin, 4096)
                if not data or ESCAPE in data:
                    break
                qemu._write(data)
    finally:
        termios.tcsetattr(stdin, termios.TCSADRAIN, old)


def main():
    parser = argparse.ArgumentParser(
        description="Boot a QEMU target and attach to its console"
    )
    parser.add_argument(
        "--lg-env",
        default=os.environ.get("LG_ENV"),
        required="LG_ENV" not in os.environ,
    )
    parser.add_argument(
        "--firmware",
        default=os.environ.get("LG_IMAGE"),
        required="LG_IMAGE" not in os.environ,
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show boot console output"
    )
    args = parser.parse_args()

    labgrid.logging.basicConfig(level=logging.CONSOLE if args.verbose else logging.INFO)
    labgrid.logging.StepLogger.start()

    env = Environment(config_file=args.lg_env)
    env.config.data.setdefault("images", {})["firmware"] = os.path.abspath(
        args.firmware
    )
    if "LG_QEMU_BIN" in os.environ:
        env.config.data.setdefault("tools", {})["qemu_bin"] = os.environ["LG_QEMU_BIN"]

    target = env.get_target("main")
    strategy = target.get_driver("Strategy")
    try:
        strategy.transition("shell")

        ssh = strategy.ssh.networkservice
        print(f"\nSSH: ssh -p {ssh.port} root@{ssh.address}")
        print("Connected to serial console, press Ctrl-] to exit.\n")
        interact(strategy.qemu)
    finally:
        strategy.transition("off")


if __name__ == "__main__":
    main()
