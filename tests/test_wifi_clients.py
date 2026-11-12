"""
The DUT's access point with real Wi-Fi clients: they connect with each
encryption, wrong keys are rejected, many clients at once, client isolation,
and clients stay connected or come back when Wi-Fi is reloaded or restarted.

The lab's Wi-Fi probe (contrib/labgrid-probe, "Wi-Fi probe") joins the DUT's
AP with one or many stations at once.

- The probe is a place of its own (role=wifi-probe). The `wifi_probe`
  fixture (wifi_probe_fixtures.py) reserves and locks it for this module.
  QEMU and labs without one skip.
- The probe's radios are ath9k (802.11n): 2.4 and 5 GHz, no 6 GHz. Each
  test runs per band the target declares (wifi-2g, wifi-5g) and the probe
  has.
- `WifiBed.start()` brings up the DUT's AP with an SSID of its own and
  locks the stations to its BSSID, so DUTs nearby don't get in the way.

Measured numbers (association and lease times, the DUT's memory per client)
go to record_property.
"""

# ruff: noqa: F811 (tests take the imported fixture as an argument)

import ipaddress
import json
import os
import time

import pytest
from labgrid.driver.exception import ExecutionError
from probe_fixtures import DUT_LAN, DUT_LAN_IP
from test_wifi import (
    AP_KEY,
    band_params,
    find_ap,
    open_ap,
    wifi_radio_for_band,
    wifi_reset,  # noqa: F401 (autouse fixture: fresh APs for every test)
)
from wifi_probe_fixtures import (
    PROBE_BANDS,
    WifiBed,
    dut_mem_available_kb,
    not_connected,
    not_leased,
    summary,
    wifi_probe,  # noqa: F401 (fixture)
)

# Stations for test_wifi_clients_many. The probe manages about 64 per radio,
# but the TL-WDR4300 probe rebooted when 32 of them pinged at once.
CLIENTS = int(os.environ.get("WIFI_PROBE_CLIENTS", "16"))
# How long a station that must not get in gets to try
REJECT_SECONDS = 30
WRONG_KEY = "not-the-key-42"

BANDS = [p for p in band_params() if p.values[0] in PROBE_BANDS]

# (AP encryption, station encryption) pairs that must connect
COMPATIBLE = [
    ("none", "none"),
    ("psk2", "psk2"),
    ("psk2", "psk-mixed"),
    ("psk-mixed", "psk2"),
    ("psk-mixed", "psk-mixed"),
    ("sae-mixed", "psk2"),
    ("sae-mixed", "sae"),
    ("sae-mixed", "sae-mixed"),
    ("sae", "sae"),
    ("sae", "sae-mixed"),
]
# (AP encryption, station encryption, station key) that must not get in
REJECTED = [
    ("psk2", "psk2", WRONG_KEY),
    ("sae", "sae", WRONG_KEY),
    ("sae", "psk2", AP_KEY),  # WPA3-only AP, WPA2-only client
]


def per_band(cases, idfn):
    params = []
    for band_param in BANDS:
        band = band_param.values[0]
        for case in cases:
            params.append(
                pytest.param(
                    band, *case, marks=band_param.marks, id=f"{band}-{idfn(*case)}"
                )
            )
    return params


def assert_all_up(status, leases=True, dut_log=None):
    """All stations connected (and leased). dut_log: a callable returning
    the DUT's side for the failure message (dut_diagnostics(), WifiBed.diagnose)."""
    down = not_connected(status)
    if down:
        text = f"{len(down)} stations not connected ({summary(status)}): {down}"
        if dut_log:
            text += f"\n{dut_log()}"
        pytest.fail(text)
    if leases:
        missing = not_leased(status)
        if missing:
            text = f"{len(missing)} stations without a lease ({summary(status)}): {missing}"
            text += f"\nprobe udhcpc log:\n{status.get('dhcp_log') or '(none)'}"
            if dut_log:
                text += f"\n{dut_log()}"
            pytest.fail(text)


def dut_diagnostics(shell_command):
    """The DUT's DHCP and Wi-Fi side, over the serial console (works when
    SSH doesn't)."""
    # Packet counters twice, 5 s apart: does the LAN side still receive?
    counters = (
        "for i in eth1 br-lan phy0-ap0 phy1-ap0; do "
        "[ -d /sys/class/net/$i ] && echo $i rx $(cat /sys/class/net/$i/statistics/rx_packets)"
        " tx $(cat /sys/class/net/$i/statistics/tx_packets); done"
    )
    cmds = (
        "logread | grep -iE 'dnsmasq|dhcp|hostapd|netifd' | tail -n 40",
        "logread | tail -n 20",
        "dmesg | tail -n 30",
        "uptime; free",
        "wc -l < /tmp/dhcp.leases",
        "ip neigh show dev br-lan",
        counters,
        "sleep 5",
        counters,
        (
            "for i in phy0-ap0 phy1-ap0; do [ -d /sys/class/net/$i ] && "
            "echo $i stations $(iw dev $i station dump | grep -c ^Station); done"
        ),
    )
    out = []
    for cmd in cmds:
        try:
            stdout, _, rc = shell_command.run(cmd, timeout=30)
            out.append(f"# {cmd} (rc {rc})\n" + "\n".join(stdout))
        except (ExecutionError, OSError) as e:  # the console may be gone too
            out.append(f"# {cmd}: {e}")
    return "DUT:\n" + "\n".join(out)


def assert_all_ping(bed, **kwargs):
    reply = bed.ping(**kwargs)
    failed = [r for r in reply["results"] if r["received"] == 0]
    assert not failed, (
        f"{len(failed)} of {len(reply['results'])} stations can't ping {reply['host']}: {failed}"
    )
    return reply


def carrier_changes(status):
    return {s["ifname"]: s["carrier_changes"] for s in status["stations"]}


# --- encryption ---------------------------------------------------------------


@pytest.mark.parametrize(
    "band,ap_encryption,sta_encryption",
    per_band(COMPATIBLE, lambda ap, sta: f"ap_{ap}-sta_{sta}"),
)
def test_wifi_client_encryption(
    ssh_command, wifi_probe, target, band, ap_encryption, sta_encryption
):
    """
    For each band and each compatible pair of AP and client encryption (open,
    WPA2, WPA2/WPA3 mixed, WPA3), a real client joins, gets an address from
    the device and reaches it. If it fails, clients with that security
    setting can't use the network.

    The DUT lists the client as authorized; SAE clients use management frame
    protection (WPA3 requires it).
    """
    bed = WifiBed.start(
        ssh_command,
        wifi_probe,
        target,
        band,
        ap_encryption,
        sta_encryption=sta_encryption,
    )
    status = bed.wait()
    assert_all_up(status, dut_log=bed.diagnose)
    assert_all_ping(bed)

    mac = status["stations"][0]["mac"].lower()
    clients = bed.dut_clients()
    assert mac in clients, f"DUT doesn't list {mac}: {sorted(clients)}"
    assert clients[mac]["authorized"], clients[mac]
    # Both sides speak SAE: wpa_supplicant prefers it over PSK
    if "sae" in sta_encryption and "sae" in ap_encryption:
        assert clients[mac]["mfp"], f"SAE client without MFP: {clients[mac]}"


@pytest.mark.xfail(
    reason="upstream bug: hostapd keeps the WPA group keys of a BSS "
    "reconfigured to open",
    strict=False,
)
@pytest.mark.parametrize("band", BANDS)
def test_wifi_client_open_after_wpa(
    ssh_command, wifi_probe, target, band, record_property
):
    """
    For each band, an AP switched from WPA3 to open without a restart still
    serves clients. If it fails, changing a network to open leaves clients
    connected but without an address until Wi-Fi is restarted.

    Regression (not filed upstream yet). hostapd reconfigures the BSS on
    the same netdev and leaves the WPA group keys (GTK, IGTK) in mac80211,
    which then drops the open clients' unencrypted frames ("rx drop misc"):
    they associate but get no lease.

    wifi_reset gives every other test fresh APs; this one reconfigures in
    place on purpose.
    """
    first = WifiBed.start(ssh_command, wifi_probe, target, band, "sae")
    assert_all_up(first.wait(), dut_log=first.diagnose)

    bed = WifiBed.start(ssh_command, wifi_probe, target, band, "none")
    keys = bed.kernel_keys()
    record_property("dut_keys_on_open_ap", json.dumps(keys))
    status = bed.wait()
    assert_all_up(status, dut_log=bed.diagnose)
    assert_all_ping(bed)
    assert not keys, (
        f"mac80211 still has keys on the phy of the open AP {bed.ap_ifname()}"
        f" (algorithm, keyidx): {keys}"
    )


@pytest.mark.parametrize(
    "band,ap_encryption,sta_encryption,sta_key",
    per_band(
        REJECTED,
        lambda ap, sta, key: (
            f"ap_{ap}-sta_{sta}" + ("-wrong_key" if key == WRONG_KEY else "")
        ),
    ),
)
def test_wifi_client_rejected(
    ssh_command, wifi_probe, target, band, ap_encryption, sta_encryption, sta_key
):
    """
    For each band, a client with the wrong passphrase, or a WPA2-only client
    on a WPA3-only AP, doesn't get in. If it fails, the network admits
    clients it should keep out, a security problem.

    Not connected after REJECT_SECONDS, and the DUT lists it as not
    authorized (or not at all).
    """
    bed = WifiBed.start(
        ssh_command,
        wifi_probe,
        target,
        band,
        ap_encryption,
        sta_encryption=sta_encryption,
        sta_key=sta_key,
    )
    time.sleep(REJECT_SECONDS)
    status = bed.status()
    assert status["connected"] == 0, f"rejected client connected: {summary(status)}"
    mac = status["stations"][0]["mac"].lower()
    assert mac not in bed.dut_authorized(), f"DUT authorized {mac}"


@pytest.mark.parametrize("band", BANDS)
def test_wifi_client_hidden_ssid(ssh_command, wifi_probe, target, band):
    """For each band, a client that asks for the network by name joins an AP
    with a hidden SSID. If it fails, hidden networks are unusable."""
    bed = WifiBed.start(
        ssh_command,
        wifi_probe,
        target,
        band,
        "psk2",
        options={"hidden": 1},
        hidden=True,
    )
    assert_all_up(bed.wait())
    assert_all_ping(bed)


# --- many clients ---------------------------------------------------------------


@pytest.mark.parametrize("band", BANDS)
# Open first: it needs no keys, so it tells scale problems apart from
# encryption ones (many stations' pairwise keys on one radio)
@pytest.mark.parametrize("encryption", ["none", "psk2"])
def test_wifi_clients_many(
    shell_command, ssh_command, wifi_probe, target, band, encryption, record_property
):
    """
    For each band, open and WPA2, many clients (16 by default) join the AP
    at once and all of them get an address and reach the device. If it
    fails, the device can't handle a busy network, e.g. in a home with
    many devices or after a power cut when all reconnect together.

    CLIENTS stations (WIFI_PROBE_CLIENTS): all connect, all get distinct
    leases in the DUT's LAN, all ping the DUT, and the DUT lists every one
    as authorized and in its DHCP leases. Open runs first: it needs no
    keys, so it tells scale problems apart from encryption ones.
    """
    mem_before = dut_mem_available_kb(ssh_command)
    bed = WifiBed.start(
        ssh_command, wifi_probe, target, band, encryption, count=CLIENTS
    )
    status = bed.wait(timeout=max(180, CLIENTS * 4))
    for key in ("first_connected_s", "all_connected_s", "all_leases_s"):
        record_property(f"wifi_clients_{key}", status.get(key))
    record_property("wifi_clients", CLIENTS)
    assert_all_up(status, dut_log=lambda: dut_diagnostics(shell_command))

    ips = [s["ip"] for s in status["stations"]]
    assert len(set(ips)) == len(ips), f"duplicate leases: {sorted(ips)}"
    outside = [ip for ip in ips if ipaddress.ip_address(ip) not in DUT_LAN]
    assert not outside, f"leases outside {DUT_LAN}: {outside}"

    assert_all_ping(bed)

    macs = {s["mac"].lower() for s in status["stations"]}
    missing = macs - bed.dut_authorized()
    assert not missing, (
        f"DUT doesn't list {len(missing)} clients as authorized: {sorted(missing)}"
    )
    leases = "\n".join(ssh_command.run("cat /tmp/dhcp.leases")[0]).lower()
    missing = [mac for mac in macs if mac not in leases]
    assert not missing, f"no DHCP lease on the DUT for {sorted(missing)}"

    mem_after = dut_mem_available_kb(ssh_command)
    record_property("dut_mem_available_kb", mem_after)
    record_property(
        "dut_mem_per_client_kb", round((mem_before - mem_after) / CLIENTS, 1)
    )


@pytest.mark.xfail(
    reason="known problem: isolated clients still reach each other (isolate=1)",
    strict=False,
)
@pytest.mark.parametrize("band", BANDS)
def test_wifi_clients_isolation(ssh_command, wifi_probe, target, band):
    """
    For each band, client isolation (`option isolate 1`) keeps Wi-Fi clients
    from reaching each other while they still reach the device. If it fails,
    guest or public networks don't protect their clients from each other.

    Two clients reach each other over the AP; with isolate=1 they don't,
    and both still reach the DUT. The isolated half only counts once
    the open half has shown the path works.

    Known problem (not filed upstream yet): with isolate=1 the clients still
    reach each other.
    """
    for isolate in (0, 1):
        bed = WifiBed.start(
            ssh_command,
            wifi_probe,
            target,
            band,
            "psk2",
            count=2,
            options={"isolate": isolate},
        )
        status = bed.wait()
        assert_all_up(status)
        assert_all_ping(bed)

        a, b = status["stations"][0], status["stations"][1]
        before = peer_packets(bed, a, b)
        reply = bed.ping(host=b["ip"], stations=[0], diag=True)
        after = peer_packets(bed, a, b)
        ping = reply["results"][0]
        moved = {k: after[k] - before[k] for k in before}
        # The probe's own view (counters, rules, neighbours, routes; older
        # probe code: none) goes to the captured output, not the message
        print(f"probe diagnostics, ping {a['ip']} -> {b['ip']}:")
        print(json.dumps(reply.get("diag"), indent=1))
        if isolate and ping["received"]:
            ap = ap_isolation_state(bed)
            print(f"DUT AP {ap['ifname']}: {ap}")
            pytest.fail(isolation_leak(a, b, ping, moved, ap))
        if not isolate and not ping["received"]:
            pytest.fail(
                f"isolate=0, but {a['ip']} can't reach its peer {b['ip']} through the AP: "
                f"{ping_text(ping)}\n{forwarding_text(moved)}"
            )


def ping_text(ping):
    return f"{ping['received']} of {ping['transmitted']} pings answered"


def forwarding_text(moved):
    """hostapd's packet counters during the ping, as a path"""
    return (
        "hostapd counted during the ping: "
        f"from A {moved['a_rx']}, to B {moved['b_tx']}, "
        f"from B {moved['b_rx']}, to A {moved['a_tx']} packets"
    )


def isolation_leak(a, b, ping, moved, ap):
    """Why an isolated client reached its peer, first line first"""
    lines = [
        f"isolate=1, but {a['ip']} ({a['ifname']}) still reaches {b['ip']}: {ping_text(ping)}",
        forwarding_text(moved),
        (
            f"hostapd {ap['ifname']}: ap_isolate={ap['ap_isolate'] or '(not set)'}; "
            f"uci: {ap['uci_isolate'] or 'no isolate option'}"
        ),
        (
            f"bridge port {ap['ifname']}: isolated={ap['isolated'] or '?'}, "
            f"hairpin_mode={ap['hairpin_mode'] or '?'}, "
            f"multicast_to_unicast={ap['multicast_to_unicast'] or '?'}"
        ),
    ]
    if ap["ap_isolate"] != "1":
        lines.append("likely cause: hostapd runs this AP without ap_isolate")
    elif ap["hairpin_mode"] == "1" and ap["isolated"] != "1":
        lines.append(
            "likely cause: the bridge sends client traffic back out of the AP "
            "(hairpin_mode=1) and the port is not isolated (isolated=0)"
        )
    return "\n".join(lines)


def ap_isolation_state(bed):
    """How the DUT runs the AP: hostapd's isolation setting for this
    interface, the uci option, and the bridge port flags that decide
    whether traffic between clients comes back out (hairpin_mode) or is
    blocked (isolated). Values are strings, "" where there is none."""
    ifname = bed.ap_ifname()
    state = {"ifname": ifname}
    # hostapd's config: one interface= (first) or bss= block per AP
    stdout, _, _ = bed.ssh.run("cat /var/run/hostapd-*.conf")
    current = None
    state["ap_isolate"] = ""
    for line in stdout:
        key, _, value = line.partition("=")
        if key in ("interface", "bss"):
            current = value
        elif key == "ap_isolate" and current == ifname:
            state["ap_isolate"] = value
    # e.g. "wireless.default_radio0.isolate='1'", one per AP that sets it
    stdout, _, _ = bed.ssh.run("uci show wireless | grep '[.]isolate='")
    state["uci_isolate"] = ", ".join(stdout)
    for flag in ("isolated", "hairpin_mode", "multicast_to_unicast"):
        stdout, _, _ = bed.ssh.run(f"cat /sys/class/net/{ifname}/brport/{flag}")
        state[flag] = stdout[0].strip() if stdout else ""
    return state


def peer_packets(bed, a, b):
    """hostapd's packet counters for stations a and b"""
    clients = bed.dut_clients()
    pa = clients.get(a["mac"].lower(), {}).get("packets", {})
    pb = clients.get(b["mac"].lower(), {}).get("packets", {})
    return {
        "a_rx": pa.get("rx", 0),
        "b_tx": pb.get("tx", 0),
        "b_rx": pb.get("rx", 0),
        "a_tx": pa.get("tx", 0),
    }


@pytest.mark.parametrize("band", BANDS)
def test_wifi_clients_maxassoc(ssh_command, wifi_probe, target, band):
    """For each band, the client limit (`option maxassoc 4`) holds: of 6
    clients exactly 4 get in. If it fails, the limit an admin sets has no
    effect, or the AP admits fewer clients than allowed."""
    limit = 4
    bed = WifiBed.start(
        ssh_command,
        wifi_probe,
        target,
        band,
        "psk2",
        count=limit + 2,
        options={"maxassoc": limit},
    )
    bed.wait(count=limit, leases=False)
    # Let the others try for a while
    time.sleep(REJECT_SECONDS)
    status = bed.status()
    assert status["connected"] == limit, f"maxassoc {limit}: {summary(status)}"
    authorized = bed.dut_authorized()
    assert len(authorized) == limit, (
        f"DUT lists {len(authorized)} authorized clients: {sorted(authorized)}"
    )


# --- clients across reloads -----------------------------------------------------


@pytest.mark.parametrize("band", BANDS)
def test_wifi_reload_keeps_clients(ssh_command, wifi_probe, target, band):
    """For each band, `wifi reload` without a config change doesn't
    disconnect any client. If it fails, every config apply (e.g. from LuCI)
    interrupts all Wi-Fi users even when nothing changed for them."""
    bed = WifiBed.start(ssh_command, wifi_probe, target, band, "psk2", count=8)
    status = bed.wait()
    assert_all_up(status)
    before = carrier_changes(status)

    ssh_command.run_check("wifi reload")
    time.sleep(20)

    status = bed.status()
    after = carrier_changes(status)
    dropped = sorted(i for i in before if after.get(i) != before[i])
    assert not dropped, (
        f"wifi reload without changes disconnected {dropped} ({summary(status)})"
    )
    assert_all_ping(bed)


@pytest.mark.parametrize("band", BANDS)
def test_wifi_restart_clients_reconnect(
    ssh_command, wifi_probe, target, band, record_property
):
    """For each band, after `wifi down` and `wifi up` every client comes back
    and reaches the device. If it fails, clients stay offline after a Wi-Fi
    restart. The reconnect time is recorded."""
    bed = WifiBed.start(ssh_command, wifi_probe, target, band, "psk2", count=8)
    assert_all_up(bed.wait())

    ssh_command.run_check("wifi down")
    gone = bed.wait_connected_below(1, timeout=30)
    assert gone["connected"] == 0, (
        f"clients still connected after wifi down: {summary(gone)}"
    )
    started = time.monotonic()
    ssh_command.run_check("wifi up")

    status = bed.wait(timeout=120)
    record_property("wifi_reconnect_s", round(time.monotonic() - started, 1))
    assert_all_up(status)
    assert_all_ping(bed)


@pytest.mark.lg_feature(["wifi-2g", "wifi-5g"])
def test_wifi_radio_disable_keeps_other_band(ssh_command, wifi_probe, target):
    """
    Turning off the 2.4 GHz radio disconnects only its clients: the 5 GHz
    clients stay connected, and the 2.4 GHz clients come back once it is
    on again. If it fails, changing one radio disrupts the other band.
    """
    bed2 = WifiBed.start(ssh_command, wifi_probe, target, "2g", "psk2", count=2)
    bed5 = WifiBed.start(ssh_command, wifi_probe, target, "5g", "psk2", count=2)
    assert_all_up(bed2.wait())
    status5 = bed5.wait()
    assert_all_up(status5)
    before = carrier_changes(status5)

    radio, _ = wifi_radio_for_band(ssh_command, "2g")
    ssh_command.run_check(f"uci set wireless.{radio}.disabled=1")
    ssh_command.run_check("uci commit wireless")
    ssh_command.run_check("wifi reload")
    try:
        gone = bed2.wait_connected_below(1, timeout=30)
        assert gone["connected"] == 0, (
            f"2g clients still connected with the radio off: {summary(gone)}"
        )
        time.sleep(5)
        status5 = bed5.status()
        after = carrier_changes(status5)
        dropped = sorted(i for i in before if after.get(i) != before[i])
        assert not dropped, f"disabling 2g disconnected 5g clients {dropped}"
        assert_all_ping(bed5)
    finally:
        ssh_command.run(f"uci -q delete wireless.{radio}.disabled")
        ssh_command.run("uci commit wireless")
        ssh_command.run("wifi reload")

    status2 = bed2.wait(timeout=120)
    assert_all_up(status2)
    assert_all_ping(bed2)


@pytest.mark.lg_feature("wifi-2g")
def test_wifi_clients_follow_channel_change(ssh_command, wifi_probe, target):
    """
    After the AP moves from channel 1 to channel 11, its clients find it on
    the new channel and reach the device again. If it fails, changing the
    channel leaves clients disconnected.
    """
    bed = WifiBed.start(
        ssh_command, wifi_probe, target, "2g", "psk2", count=4, lock_freq=False
    )
    assert_all_up(bed.wait())

    open_ap(ssh_command, "2g", "psk2", ssid=bed.ssid, channel=11)
    deadline = time.monotonic() + 60
    freq = None
    while time.monotonic() < deadline:
        _, freq = find_ap(ssh_command, bed.ssid)
        if freq == 2462:
            break
        time.sleep(2)
    assert freq == 2462, f"AP on {freq} MHz, not channel 11 (2462 MHz)"

    status = bed.wait(timeout=120)
    assert_all_up(status)
    status = bed.status(detail=True)
    wrong = {s["ifname"]: s.get("link", {}).get("freq") for s in status["stations"]}
    wrong = {i: f for i, f in wrong.items() if f != 2462}
    assert not wrong, f"stations not on 2462 MHz: {wrong}"
    assert_all_ping(bed)


# --- counters -------------------------------------------------------------------


@pytest.mark.parametrize("band", BANDS)
def test_wifi_client_counters(ssh_command, wifi_probe, target, band):
    """
    For each band, the device's per-client traffic counters count at least
    the traffic a client sent and received. If it fails, statistics in LuCI
    and monitoring tools are wrong.

    hostapd get_clients byte counters, 10 pings of 1000 bytes.
    """
    bed = WifiBed.start(ssh_command, wifi_probe, target, band, "psk2")
    status = bed.wait()
    assert_all_up(status)
    mac = status["stations"][0]["mac"].lower()

    before = bed.dut_clients()[mac]["bytes"]
    assert_all_ping(bed, count=10, size=1000)
    after = bed.dut_clients()[mac]["bytes"]

    minimum = 10 * 1000
    rx, tx = after["rx"] - before["rx"], after["tx"] - before["tx"]
    assert rx >= minimum, f"DUT counted {rx} bytes from the client, sent >= {minimum}"
    assert tx >= minimum, f"DUT counted {tx} bytes to the client, sent >= {minimum}"


@pytest.mark.parametrize("band", BANDS)
def test_wifi_client_on_dut_lan(ssh_command, wifi_probe, target, band):
    """For each band, a Wi-Fi client gets an address in the device's LAN with
    the device as its router, i.e. the AP is bridged into the LAN. If it
    fails, Wi-Fi clients end up in the wrong network or without Internet."""
    bed = WifiBed.start(ssh_command, wifi_probe, target, band, "psk2")
    status = bed.wait()
    assert_all_up(status)
    lease = status["stations"][0]["lease"]
    assert ipaddress.ip_address(lease["ip"]) in DUT_LAN, lease
    assert lease["ip"] != DUT_LAN_IP, lease
    assert lease["router"][:1] == [DUT_LAN_IP], lease
