"""Security of the DUT's network services, as seen from outside."""

import json
import re
import subprocess
from collections import defaultdict


def test_ssh_supported_algorithms(env, ssh_command, record_property):
    """The SSH server offers modern key exchange, host key, cipher and MAC
    algorithms (e.g. curve25519, Ed25519, chacha20-poly1305, the strict-KEX
    fix for the Terrapin attack); records everything it offers.

    A missing algorithm means current SSH clients may fail to connect or fall
    back to weaker cryptography. Ed25519 isn't required on small-flash
    targets, whose dropbear is built without it.
    """
    with ssh_command.forward_local_port(22) as localport:
        # "+" runs the script on the forwarded port without -sV (its port
        # rule wants 22 or a detected ssh service). -sV sets up DTLS, which
        # aborts nmap on hosts whose OpenSSL has none (OpenWrt lab hosts).
        output = subprocess.check_output(
            f"nmap --script +ssh2-enum-algos -p {localport} 127.0.0.1",
            text=True,
            shell=True,
        )

        pattern = re.compile(
            r"""
        ^\|\s{2,}(?P<category>\w+_algorithms):\s\(\d+\)     # Algorithm category
        |^\|\s{7}(?P<algorithm>[^\n|]+)                     # Algorithm entries
        """,
            re.MULTILINE | re.VERBOSE,
        )

        algorithms = defaultdict(list)
        current_category = None

        for match in pattern.finditer(output):
            if match.group("category"):
                current_category = match.group("category")
            elif match.group("algorithm") and current_category:
                algorithms[current_category].append(match.group("algorithm").strip())

        algorithms = dict(algorithms)
        for category, names in algorithms.items():
            record_property(f"ssh_{category}", json.dumps(names))

        assert "curve25519-sha256" in algorithms["kex_algorithms"]
        assert "curve25519-sha256@libssh.org" in algorithms["kex_algorithms"]
        assert "diffie-hellman-group14-sha256" in algorithms["kex_algorithms"]
        assert "kexguess2@matt.ucc.asn.au" in algorithms["kex_algorithms"]
        assert "kex-strict-s-v00@openssh.com" in algorithms["kex_algorithms"]

        # small_flash targets build dropbear without Ed25519 by default
        if "small_flash" not in env.get_target_features():
            assert "ssh-ed25519" in algorithms["server_host_key_algorithms"]
        assert "rsa-sha2-256" in algorithms["server_host_key_algorithms"]

        assert "chacha20-poly1305@openssh.com" in algorithms["encryption_algorithms"]
        assert "aes128-ctr" in algorithms["encryption_algorithms"]
        assert "aes256-ctr" in algorithms["encryption_algorithms"]

        assert "hmac-sha2-256" in algorithms["mac_algorithms"]
