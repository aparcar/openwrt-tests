# ruff: noqa: F811 (the test takes the imported `online` fixture as an argument)
"""Regression test for the OpenSSH over-long password acceptance bug.

openwrt/openwrt uses musl, and its openssh-server is built without PAM. On
that combination OpenSSH before 10.6p2 could treat a password longer than
musl crypt()'s limit as matching an account whose login is disabled, so a
locked account would authenticate (CVE-2026-123158, "SSHole"). OpenWrt's
default SSH server is dropbear, which this does not touch, so the check
only runs where openssh-server is actually installed.

The test stands up a throwaway sshd on 127.0.0.1:2222 with two accounts:
one with a normal password (the control) and one whose login is disabled
(shadow field "*"). It then checks, all on the DUT over the lab's existing
key-based connection:

  - the control account authenticates with its real password (so the
    password path works and a refusal below is meaningful), and
  - the locked account does NOT authenticate with an over-long password.

If the control login can't be made to work (package missing, sshd won't
start on this build), the test skips: it only asserts the security property
when it could prove the setup is sound. Everything is on loopback and torn
down afterwards.
"""

import pytest
from probe_fixtures import online, slot, wan_slot  # noqa: F401 (fixtures)

SSHD_PORT = 2222
CONTROL_USER = "ssh_ok_probe"
LOCKED_USER = "ssh_locked_probe"
CONTROL_PASSWORD = "Correct-Horse-Battery-42"
# longer than musl crypt()'s input limit; the content is irrelevant
LONG_PASSWORD_LEN = 1024
RUN_DIR = "/tmp/ssh-password-test"
MARKER = "SSH_AUTH_OK"

# ssh client options for an isolated password-only attempt to the loopback sshd
SSH_OPTS = (
    f"-p {SSHD_PORT} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
    "-o PreferredAuthentications=password -o PubkeyAuthentication=no "
    "-o NumberOfPasswordPrompts=1 -o ConnectTimeout=10"
)


def _try_login(ssh_command, user, password):
    """Attempt a password login to the DUT's loopback sshd as `user`.
    Returns True if the account authenticated (the marker command ran)."""
    out, _, _ = ssh_command.run(
        f"SSHPASS={password} sshpass -e ssh {SSH_OPTS} "
        f"{user}@127.0.0.1 'echo {MARKER}' 2>/dev/null",
        timeout=30,
    )
    return MARKER in "\n".join(out)


def _setup(ssh_command):
    """Install openssh-server and start a loopback sshd with the two test
    accounts. Returns True on success; the caller tears it down either way."""
    rc = ssh_command.run(
        "apk update && apk add openssh-server openssh-client sshpass", timeout=300
    )[2]
    if rc != 0:
        return False
    # a long password with no shell-special bytes
    setup = f"""
set -e
mkdir -p {RUN_DIR}
ssh-keygen -A
cat > {RUN_DIR}/sshd_config <<EOF
Port {SSHD_PORT}
ListenAddress 127.0.0.1
PasswordAuthentication yes
PermitRootLogin no
PidFile {RUN_DIR}/sshd.pid
EOF
hash=$(cryptpw -m sha512 '{CONTROL_PASSWORD}')
printf '%s:x:5061:5061:probe:{RUN_DIR}:/bin/ash\\n' {CONTROL_USER} >> /etc/passwd
printf '%s:x:5062:5062:probe:{RUN_DIR}:/bin/ash\\n' {LOCKED_USER} >> /etc/passwd
printf '%s:%s:19000:0:99999:7:::\\n' {CONTROL_USER} "$hash" >> /etc/shadow
printf '%s:*:19000:0:99999:7:::\\n' {LOCKED_USER} >> /etc/shadow
/usr/sbin/sshd -f {RUN_DIR}/sshd_config
sleep 2
[ -s {RUN_DIR}/sshd.pid ]
"""
    return ssh_command.run(setup, timeout=120)[2] == 0


def _teardown(ssh_command):
    ssh_command.run(
        f"[ -s {RUN_DIR}/sshd.pid ] && kill $(cat {RUN_DIR}/sshd.pid); "
        f"sed -i '/^{CONTROL_USER}:/d;/^{LOCKED_USER}:/d' /etc/passwd /etc/shadow; "
        f"rm -rf {RUN_DIR} /etc/ssh/ssh_host_*; "
        "apk del openssh-server openssh-client sshpass",
        timeout=120,
    )


def test_openssh_rejects_overlong_password(online, ssh_command, record_property):
    """A locked account (login disabled) is not authenticated by an over-long
    password. A device whose openssh-server accepts it lets anyone log in to
    such accounts, e.g. root when only key login is meant (CVE-2026-123158).

    openssh-server only; the default dropbear is not affected and makes the
    test skip.
    """
    long_password = "A" * LONG_PASSWORD_LEN
    try:
        if not _setup(ssh_command):
            pytest.skip("openssh-server not available or won't start on this build")

        if not _try_login(ssh_command, CONTROL_USER, CONTROL_PASSWORD):
            pytest.skip("control password login did not work; can't validate")
        record_property("control_login", "ok")

        accepted = _try_login(ssh_command, LOCKED_USER, long_password)
        assert not accepted, (
            f"the locked account {LOCKED_USER} authenticated with a "
            f"{LONG_PASSWORD_LEN}-char password: openssh-server accepts an "
            "over-long password (CVE-2026-123158), update it to 10.6p2 or later"
        )
        # a wrong over-long password for a real account is refused too
        assert not _try_login(ssh_command, CONTROL_USER, long_password), (
            f"{CONTROL_USER} authenticated with a wrong {LONG_PASSWORD_LEN}-char "
            "password"
        )
    finally:
        _teardown(ssh_command)
