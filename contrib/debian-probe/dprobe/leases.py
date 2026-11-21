# SPDX-License-Identifier: LGPL-2.1-only
"""Lease file parsers for the WAN servers. Pure functions."""

import csv
import io


def dnsmasq(text):
    """dnsmasq leasefile -> [{expires, mac, ip, hostname, client_id}].

    Format: '<expiry epoch> <mac> <ip> <hostname|*> <client-id|*>' per line;
    a 'duid ...' line belongs to DHCPv6 and is skipped.
    """
    leases = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] == "duid":
            continue
        leases.append(
            {
                "expires": int(parts[0]),
                "mac": parts[1].lower(),
                "ip": parts[2],
                "hostname": None if parts[3] == "*" else parts[3],
                "client_id": None if len(parts) < 5 or parts[4] == "*" else parts[4],
            }
        )
    return leases


# lease_type in kea's memfile: 0 IA_NA, 1 IA_TA, 2 IA_PD
KEA_LEASE_TYPES = {"0": "na", "1": "ta", "2": "pd"}


def kea6(text):
    """kea-dhcp6 memfile CSV -> current leases, latest record per lease.

    The memfile is append-only: each renew/release appends a row, and
    valid_lifetime 0 marks a released/expired lease. Returns
    [{type, address, prefix_len, duid, iaid, valid, preferred, expire,
    state}] with the last row per (type, address).
    """
    latest = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        if not row.get("address"):
            continue
        kind = KEA_LEASE_TYPES.get(row.get("lease_type", ""), row.get("lease_type"))
        lease = {
            "type": kind,
            "address": row["address"],
            "prefix_len": int(row.get("prefix_len") or 128),
            "duid": row.get("duid"),
            "iaid": int(row.get("iaid") or 0),
            "valid": int(row.get("valid_lifetime") or 0),
            "preferred": int(row.get("pref_lifetime") or 0),
            "expire": int(row.get("expire") or 0),
            "state": int(row.get("state") or 0),
        }
        latest[(kind, lease["address"])] = lease
    return [lease for lease in latest.values() if lease["valid"] > 0]
