import re
import subprocess
from collections import defaultdict


def test_ssh_supported_algorithms(ssh_command):
    with ssh_command.forward_local_port(22) as localport:
        output = subprocess.check_output(
            f"nmap --script ssh2-enum-algos -sV -p {localport} localhost",
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

        print(algorithms)
        assert "curve25519-sha256" in algorithms["kex_algorithms"]
        assert "curve25519-sha256@libssh.org" in algorithms["kex_algorithms"]
        assert "diffie-hellman-group14-sha256" in algorithms["kex_algorithms"]
        assert "kexguess2@matt.ucc.asn.au" in algorithms["kex_algorithms"]
        assert "kex-strict-s-v00@openssh.com" in algorithms["kex_algorithms"]

        assert "ssh-ed25519" in algorithms["server_host_key_algorithms"]
        assert "rsa-sha2-256" in algorithms["server_host_key_algorithms"]
        assert "ssh-rsa" in algorithms["server_host_key_algorithms"]

        assert "chacha20-poly1305@openssh.com" in algorithms["encryption_algorithms"]
        assert "aes128-ctr" in algorithms["encryption_algorithms"]
        assert "aes256-ctr" in algorithms["encryption_algorithms"]

        assert "hmac-sha2-256" in algorithms["mac_algorithms"]
