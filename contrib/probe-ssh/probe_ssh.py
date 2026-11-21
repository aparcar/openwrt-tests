#!/usr/bin/env python3
"""probe-ssh: the only command an SSH login as a probe's user runs (sshd
ForceCommand, ansible role labgrid_lab, tasks/probe_login.yml; also the
debian_probe role in contrib/debian-probe).

    ForceCommand /usr/local/sbin/probe-ssh iprobe

It runs the command the client asked for (SSH_ORIGINAL_COMMAND) only if it
is `<probe> <args>` (here /usr/local/bin/iprobe), `sudo -n
/usr/local/sbin/<probe> <args>` (dprobe, which runs as root through a
sudoers rule for that one command) or a bare `cat`: labgrid's SSHDriver
keeps its connection alive with a remote `cat`. All run without a shell.
Anything else, an interactive login included, is refused with exit 2.
"""

import os
import shlex
import sys

BIN = "/usr/local/bin"
SBIN = "/usr/local/sbin"
SUDO = "/usr/bin/sudo"


def allowed(probe, original):
    """SSH_ORIGINAL_COMMAND -> the argv to exec, or None if refused."""
    try:
        argv = shlex.split(original or "")
    except ValueError:  # unbalanced quotes
        return None
    if argv == ["cat"]:
        return ["cat"]
    if argv and argv[0] in (probe, f"{BIN}/{probe}"):
        return [f"{BIN}/{probe}", *argv[1:]]
    if argv[:3] in (["sudo", "-n", f"{SBIN}/{probe}"], [SUDO, "-n", f"{SBIN}/{probe}"]):
        return [SUDO, "-n", f"{SBIN}/{probe}", *argv[3:]]
    return None


def main():
    if len(sys.argv) != 2 or "/" in sys.argv[1]:
        print("usage: probe-ssh <probe command in /usr/local/bin>", file=sys.stderr)
        return 2
    original = os.environ.get("SSH_ORIGINAL_COMMAND")
    argv = allowed(sys.argv[1], original)
    if argv is None:
        print(
            f"probe-ssh: only {sys.argv[1]} runs here, not {original!r}",
            file=sys.stderr,
        )
        return 2
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    sys.exit(main())
