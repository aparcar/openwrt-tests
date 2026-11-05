"""Wi-Fi configuration regressions on mac80211_hwsim radios (docs/hwsim.md).

One QEMU VM, no lab hardware: the DUT's hwsim radios are configured through
/etc/config/wireless like a real board's, and a "peer" (station, external AP)
runs on another hwsim radio that is moved into a network namespace, so its
traffic crosses the (simulated) air instead of the DUT's own stack. hwsim
delivers frames between all radios created by one module load, whatever
namespace they are in, so no host-side medium is needed.

These tests target wifi-scripts, hostapd and netifd logic. Driver bugs
(mt76, ath10k, ...) stay on real hardware, but a hwsim result tells
"wifi-scripts bug" from "driver bug" before anyone books lab time.

Every test starts from a fresh module load and a fresh wireless config,
and the fixture puts /etc/config back afterwards.
"""

import json
import re
import time
import uuid
from dataclasses import dataclass

import pytest

pytestmark = pytest.mark.lg_feature("hwsim")

# Variants of wpad the tests switch between: basic is what boards ship,
# full has dynamic VLANs and MLO for sure, mesh is basic plus 802.11s.
WPAD = {
    "basic": "wpad-basic-mbedtls",
    "full": "wpad-mbedtls",
    "mesh": "wpad-mesh-mbedtls",
}
KEY = "openwrt4all"
PEER_NS = "hwsimpeer"
BANDS = {"2g": (2400, 2500), "5g": (5150, 5895), "6g": (5925, 7125)}
IW_CHANNEL = re.compile(r"channel (\d+) \((\d+) MHz\)(?:, width: (\d+) MHz)?")


def band_of(freq):
    for band, (low, high) in BANDS.items():
        if low <= freq <= high:
            return band
    return None


@dataclass
class Radio:
    section: str  # wireless.<section>
    path: str  # virtual/mac80211_hwsim/hwsimN
    phy: str  # phyN
    band: str | None
    index: int  # option radio of a multi-radio wiphy, else 0


@dataclass
class Iface:
    name: str
    phy: str
    type: str
    ssid: str | None
    addr: str | None
    txpower: float | None
    # (channel, MHz, width MHz) per link: one for a plain BSS, one per link
    # for an MLD
    links: list


class Bed:
    """The DUT's radios, its wireless config and the peers, over SSH."""

    def __init__(self, ssh, default_radios):
        self.ssh = ssh
        self.default_radios = default_radios
        self.peers = []

    # --- shell ---------------------------------------------------------

    def sh(self, cmd, timeout=60):
        stdout, stderr, rc = self.ssh.run(cmd, timeout=timeout)
        return "\n".join(stdout + stderr), rc

    def check(self, cmd, timeout=60):
        out, rc = self.sh(cmd, timeout)
        assert rc == 0, f"`{cmd}` failed ({rc}):\n{out}"
        return out

    def mark(self):
        """A marker in the syslog; log_since() returns what follows it."""
        token = f"hwsim-test-{uuid.uuid4().hex[:12]}"
        self.sh(f"logger -t hwsim-test {token}")
        return token

    def log_since(self, token):
        return self.sh(f"logread | sed -n '/{token}/,$p'")[0]

    # --- packages and radios ---------------------------------------------

    def use_wpad(self, variant):
        """Make `variant` the installed wpad (apk over the QEMU WAN)."""
        wanted = WPAD[variant]
        names = " ".join(WPAD.values())
        installed = self.sh(f"apk info -e {names} 2>/dev/null")[0].split()
        if wanted in installed:
            return
        out, rc = self.sh(
            "wifi down 2>/dev/null; "
            + (f"apk del {' '.join(installed)} >/dev/null 2>&1; " if installed else "")
            + f"apk update >/dev/null 2>&1; apk add {wanted} 2>&1 && "
            # ubusd must re-read the ACLs (wpad_acl.json) of a package
            # installed at run time, and wpad must run for netifd to use it
            "killall -HUP ubusd && sleep 1 && "
            "/etc/init.d/wpad enable && /etc/init.d/wpad restart && "
            "ubus -t 30 wait_for hostapd wpa_supplicant",
            timeout=300,
        )
        if rc != 0:
            # The package feed, not the firmware: an error, not a failure
            raise RuntimeError(f"cannot install {wanted}:\n{out}")

    def load(self, radios=2, multi_radio=False, mlo=False, config=True):
        """Load hwsim afresh and (config=True) give it a fresh wireless
        config: every radio disabled and without a country, no wifi-iface
        at all.

        `wifi config` (25.12) leaves the radios enabled (it disables only
        their wifi-iface) and, as hwsim radios have every band, makes them
        6g with country '00'. wifi-scripts copy the country of every
        wifi-device, disabled or not, into each hostapd config (the last
        one wins), and hostapd rejects '00' ("Invalid country_code '00'"),
        so a leftover radio would keep the radio under test from coming
        up."""
        for peer in self.peers:
            peer.close()
        self.peers = []
        params = f"radios={radios}"
        if multi_radio:
            params += " multi_radio=1"
        if mlo:
            params += " mlo=1"
        out, rc = self.sh(
            "wifi down 2>/dev/null; rmmod mac80211_hwsim 2>/dev/null; "
            f"insmod mac80211_hwsim {params} 2>&1",
            timeout=60,
        )
        flags = [p for p in ("multi_radio", "mlo") if p in params]
        for flag in flags:
            value = self.sh(f"cat /sys/module/mac80211_hwsim/parameters/{flag}")[0]
            if rc != 0 or value.strip() != "Y":
                pytest.skip(f"this mac80211_hwsim has no {flag} parameter: {out}")
        assert rc == 0, f"insmod mac80211_hwsim {params} failed:\n{out}"
        if not config:
            return []
        self.check(
            "sleep 2; rm -f /etc/config/wireless; wifi config; "
            "for s in $(uci -q show wireless | "
            "sed -n 's/^wireless\\.\\([^.=]*\\)=wifi-iface$/\\1/p'); do "
            "uci delete wireless.$s; done; "
            "for s in $(uci -q show wireless | "
            "sed -n 's/^wireless\\.\\([^.=]*\\)=wifi-device$/\\1/p'); do "
            "uci set wireless.$s.disabled=1; uci -q delete wireless.$s.country; "
            "done; uci commit wireless; "
            # `wifi down` above cleared netifd's autostart for every radio,
            # and reload_config (apply()) does not set it again: re-arm it
            # here (no radio is enabled, so nothing comes up yet)
            "wifi up"
        )
        found = self.radios()
        assert found, (
            "no wifi-device after `wifi config`:\n" + self.sh("uci show wireless")[0]
        )
        return found

    def radios(self):
        config = self.sh("uci -q show wireless")[0]
        phys = {}
        for line in self.sh(
            "for p in /sys/class/ieee80211/*; do echo ${p##*/} $(readlink -f $p/device); done"
        )[0].splitlines():
            parts = line.split()
            if len(parts) == 2:
                phys[parts[1].removeprefix("/sys/devices/")] = parts[0]

        def option(section, name):
            m = re.search(
                rf"^wireless\.{section}\.{name}='([^']*)'$", config, re.MULTILINE
            )
            return m.group(1) if m else None

        found = []
        for section in re.findall(
            r"^wireless\.(\w+)=wifi-device$", config, re.MULTILINE
        ):
            path = option(section, "path") or ""
            found.append(
                Radio(
                    section=section,
                    path=path,
                    phy=phys.get(path, ""),
                    band=option(section, "band"),
                    index=int(option(section, "radio") or 0),
                )
            )
        return sorted(found, key=lambda r: (r.path, r.index))

    # --- configuration ---------------------------------------------------

    def uci(self, *lines):
        batch = "\n".join(lines)
        self.check(f"uci -q batch <<'EOF'\n{batch}\nEOF")

    def set_radio(
        self, radio, channel, band=None, htmode=None, country="US", **options
    ):
        lines = [
            f"delete wireless.{radio.section}.disabled",
            f"set wireless.{radio.section}.channel={channel}",
            f"set wireless.{radio.section}.country={country}",
        ]
        if band:
            lines.append(f"set wireless.{radio.section}.band={band}")
        if htmode:
            lines.append(f"set wireless.{radio.section}.htmode={htmode}")
        lines += [f"set wireless.{radio.section}.{k}={v}" for k, v in options.items()]
        self.uci(*lines)

    def add_iface(
        self,
        name,
        devices,
        mode="ap",
        ssid=None,
        encryption="psk2",
        key=KEY,
        network="lan",
        **options,
    ):
        """A wifi-iface; `devices` is one radio or a list (an MLD)."""
        if isinstance(devices, Radio):
            devices = [devices]
        lines = [f"set wireless.{name}=wifi-iface"]
        if len(devices) == 1:
            lines.append(f"set wireless.{name}.device={devices[0].section}")
        else:
            lines += [f"add_list wireless.{name}.device={d.section}" for d in devices]
        lines.append(f"set wireless.{name}.mode={mode}")
        if ssid:
            lines.append(f"set wireless.{name}.ssid={ssid}")
        lines.append(f"set wireless.{name}.encryption={encryption}")
        if encryption != "none":
            lines.append(f"set wireless.{name}.key={key}")
        if network:
            lines.append(f"set wireless.{name}.network={network}")
        lines += [f"set wireless.{name}.{k}={v}" for k, v in options.items()]
        self.uci(*lines)

    def apply(self):
        """Commit and reload what changed (network, wireless, dhcp, fw)."""
        self.check("uci commit && reload_config", timeout=90)

    # --- state -----------------------------------------------------------

    def ifaces(self):
        phys = {}
        for line in self.sh(
            "for i in /sys/class/net/*/phy80211; do d=${i%/phy80211}; "
            "echo ${d##*/} $(cat $i/name); done"
        )[0].splitlines():
            parts = line.split()
            if len(parts) == 2:
                phys[parts[0]] = parts[1]
        found = []
        iw = self.sh("iw dev")[0]
        for block in re.split(r"\n\s*Interface ", "\n" + iw)[1:]:
            name = block.split()[0]

            def field(pattern, block=block):
                m = re.search(pattern, block, re.MULTILINE)
                return m.group(1) if m else None

            txpower = field(r"^\s*txpower ([\d.]+) dBm")
            found.append(
                Iface(
                    name=name,
                    phy=phys.get(name, ""),
                    type=field(r"^\s*type (.+)$") or "",
                    ssid=field(r"^\s*ssid (.+)$"),
                    addr=field(r"^\s*addr (\S+)"),
                    txpower=float(txpower) if txpower else None,
                    links=[
                        (int(c), int(f), int(w) if w else None)
                        for c, f, w in IW_CHANNEL.findall(block)
                    ],
                )
            )
        return found

    def wait(self, predicate, timeout=60, interval=2):
        """Poll ifaces() until predicate(ifaces) is truthy; returns its
        last result (falsy on timeout)."""
        deadline = time.monotonic() + timeout
        while True:
            result = predicate(self.ifaces())
            if result or time.monotonic() > deadline:
                return result
            time.sleep(interval)

    def wait_ap(self, ssid, timeout=60):
        """The AP iface with `ssid` once it has a channel, or None."""
        return self.wait(
            lambda ifs: next(
                (i for i in ifs if i.ssid == ssid and i.type == "AP" and i.links), None
            ),
            timeout,
        )

    def hostapd_pid(self):
        out = self.sh('ubus call service list \'{"name":"wpad"}\'')[0]
        try:
            return json.loads(out)["wpad"]["instances"]["hostapd"]["pid"]
        except (ValueError, KeyError, TypeError):
            return None

    def debug(self):
        return "\n".join(
            f"--- {cmd}\n{self.sh(cmd)[0]}"
            for cmd in ("iw dev", "uci show wireless", "logread | tail -n 40")
        )

    # --- peers -------------------------------------------------------------

    def peer(self, radio):
        """The whole wiphy of `radio` in a netns of its own (its radios
        must stay disabled in the DUT's config)."""
        peer = Peer(self, radio.phy, f"{PEER_NS}{len(self.peers)}")
        self.peers.append(peer)
        return peer

    def cleanup(self):
        for peer in self.peers:
            peer.close()
        self.peers = []
        # Back to hwsim_setup's radios and the config from before the test
        self.load(self.default_radios, config=False)
        self.sh(
            # wifi up: load() ran `wifi down`, which sticks until `wifi up`
            "cp -a /tmp/hwsim-test-config/. /etc/config/ && reload_config; wifi up",
            timeout=90,
        )


class Peer:
    """A station or an external AP on a hwsim wiphy inside a netns,
    with its own wpa_supplicant/hostapd (the wpad multicall binary)."""

    def __init__(self, bed, phy, ns):
        self.bed = bed
        self.phy = phy
        self.ns = ns
        self.ifname = f"{ns}w"
        self.pidfile = f"/tmp/{ns}.pid"
        self.conf = f"/tmp/{ns}.conf"
        bed.check(
            f"ip netns add {ns} && iw phy {phy} set netns name {ns} && "
            + self.x(f"iw phy {phy} interface add {self.ifname} type managed")
            + " && "
            + self.x(f"ip link set {self.ifname} up")
        )

    def x(self, cmd):
        return f"ip netns exec {self.ns} {cmd}"

    def _write(self, path, text):
        self.bed.check(f"cat > {path} <<'EOF'\n{text}\nEOF")

    def stop(self):
        self.bed.sh(
            f"[ -s {self.pidfile} ] && kill $(cat {self.pidfile}); rm -f {self.pidfile}; sleep 1"
        )

    def connect(self, ssid, key=KEY, encryption="psk2", timeout=45):
        """Associate with `ssid`; returns True once `iw link` says so."""
        self.stop()
        if encryption == "sae":
            auth = f'key_mgmt=SAE\n  sae_password="{key}"\n  ieee80211w=2'
        else:
            auth = f'key_mgmt=WPA-PSK\n  psk="{key}"'
        self._write(
            self.conf, f'network={{\n  ssid="{ssid}"\n  scan_ssid=1\n  {auth}\n}}'
        )
        self.bed.check(
            self.x(
                f"wpa_supplicant -B -D nl80211 -i {self.ifname} -c {self.conf} -P {self.pidfile}"
            )
        )
        return self.wait_connected(timeout)

    def wait_connected(self, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if "Connected to" in self.bed.sh(self.x(f"iw dev {self.ifname} link"))[0]:
                time.sleep(2)  # 4-way handshake
                return True
            time.sleep(2)
        return False

    def start_ap(self, ssid, channel, hw_mode="a", key=KEY):
        """A plain WPA2 AP (the "external" AP of a test)."""
        self.stop()
        self._write(
            self.conf,
            f"interface={self.ifname}\ndriver=nl80211\nssid={ssid}\n"
            f"hw_mode={hw_mode}\nchannel={channel}\nieee80211n=1\n"
            f"wpa=2\nwpa_key_mgmt=WPA-PSK\nrsn_pairwise=CCMP\nwpa_passphrase={key}",
        )
        self.bed.check(self.x(f"hostapd -B -P {self.pidfile} {self.conf}"))

    def set_addr(self, cidr):
        self.bed.check(self.x(f"ip addr add {cidr} dev {self.ifname}"))

    def dhcp(self):
        """DHCP over the air; returns the leased address (set on the
        iface) or None."""
        script = f"/tmp/{self.ns}-udhcpc.sh"
        self._write(
            script,
            '#!/bin/sh\n[ "$1" = bound ] || exit 0\n'
            'ip addr add "$ip/${mask:-24}" dev "$interface"',
        )
        self.bed.sh(f"chmod +x {script}")
        out = self.bed.sh(
            self.x(f"udhcpc -i {self.ifname} -n -q -t 10 -T 2 -s {script} 2>&1"),
            timeout=60,
        )[0]
        m = re.search(r"lease of (\S+) obtained", out)
        return m.group(1) if m else None

    def ping(self, address, count=5):
        out = self.bed.sh(self.x(f"ping -c {count} -W 2 {address} 2>&1"), timeout=60)[0]
        m = re.search(r"(\d+) packets received", out)
        return int(m.group(1)) if m else 0

    def close(self):
        self.stop()
        # Deleting the netns gives the wiphy back to the root namespace
        self.bed.sh(f"ip netns del {self.ns} 2>/dev/null; sleep 1")


@pytest.fixture
def bed(hwsim, ssh_command):
    """The hwsim bed of the DUT; puts /etc/config and the radios back.
    `hwsim` (hwsim_setup.py) installs and loads hwsim, or errors the test."""
    bed = Bed(ssh_command, hwsim)
    if bed.sh("[ -d /sys/module/mac80211_hwsim ]")[1] != 0:
        pytest.fail("the hwsim feature is set, but mac80211_hwsim is not loaded")
    bed.check(
        "rm -rf /tmp/hwsim-test-config && cp -a /etc/config /tmp/hwsim-test-config"
    )
    yield bed
    bed.cleanup()


# --- wpad-basic: what most boards ship ---------------------------------------


AP_OPTIONS = {
    "bss_transition": {"bss_transition": "1"},
    "ieee80211k": {"ieee80211k": "1"},
    "ieee80211r": {"ieee80211r": "1", "mobility_domain": "4f57", "ft_over_ds": "0"},
    "isolate": {"isolate": "1"},
}


@pytest.mark.parametrize("option", AP_OPTIONS)
def test_wifi_ap_options(bed, option, record_property):
    """Regression: openwrt/openwrt#23565

    An AP with a common option (802.11v bss_transition, 802.11k, 802.11r,
    isolate) on the default wpad-basic comes up, and hostapd does not
    reject its config with "unknown configuration item".
    """
    bed.use_wpad("basic")
    radio = bed.load()[0]
    bed.set_radio(radio, 1, band="2g", htmode="HT20")
    bed.add_iface("t_ap", radio, ssid="hwsim-opt", **AP_OPTIONS[option])
    token = bed.mark()
    bed.apply()

    ap = bed.wait_ap("hwsim-opt")
    log = bed.log_since(token)
    unknown = [
        line for line in log.splitlines() if "unknown configuration item" in line
    ]
    record_property("unknown_items", json.dumps(unknown))
    assert ap, f"AP with {option} did not come up\n{bed.debug()}"
    assert not unknown, "hostapd rejected options:\n" + "\n".join(unknown)


@pytest.mark.parametrize("unsupported", ["bss_transition", "mesh"])
def test_wifi_unsupported_option_isolated(bed, unsupported, record_property):
    """Regression: openwrt/openwrt#24625

    On wpad-basic, one wifi-iface with something basic may not support
    (802.11v bss_transition, a mesh iface) must not take down the other
    AP on the same radio. Upstream considers the radio going down
    documented behaviour, so that outcome is an xfail with the evidence
    recorded, not a failure, until it is decided.
    """
    bed.use_wpad("basic")
    radio = bed.load()[0]
    bed.set_radio(radio, 1, band="2g", htmode="HT20")
    bed.add_iface("t_clean", radio, ssid="hwsim-clean")
    if unsupported == "mesh":
        bed.add_iface(
            "t_bad",
            radio,
            mode="mesh",
            mesh_id="hwsim-mesh",
            encryption="none",
            network=None,
        )
    else:
        bed.add_iface("t_bad", radio, ssid="hwsim-bad", bss_transition="1")
    token = bed.mark()
    bed.apply()

    clean = bed.wait_ap("hwsim-clean", timeout=45)
    status = bed.sh("ubus call network.wireless status")[0]
    log = bed.log_since(token)
    errors = [
        line
        for line in log.splitlines()
        if re.search(r"FAILED|[Ee]rror|not supported|unknown", line)
    ]
    record_property("clean_vif_up", bool(clean))
    record_property("errors", json.dumps(errors[-20:]))
    record_property("wireless_status", status)
    if not clean:
        pytest.xfail(f"{unsupported} on t_bad took down the radio (#24625)")


def test_wifi_txpower_zero(bed, record_property):
    """Regression: openwrt/openwrt#22855

    `option txpower 0` must give the lowest power, not the automatic
    (maximum) one: wifi-scripts treated 0 as unset.
    """
    bed.use_wpad("basic")
    radio = bed.load()[0]
    bed.set_radio(radio, 1, band="2g", htmode="HT20")
    bed.add_iface("t_ap", radio, ssid="hwsim-txp")
    powers = {}
    for setting in ("auto", "1", "0"):
        if setting == "auto":
            bed.uci(f"delete wireless.{radio.section}.txpower")
        else:
            bed.uci(f"set wireless.{radio.section}.txpower={setting}")
        bed.apply()
        time.sleep(5)  # the reload first takes the old AP down
        ap = bed.wait_ap("hwsim-txp")
        assert ap, f"AP did not come up with txpower {setting}\n{bed.debug()}"
        powers[setting] = ap.txpower
        record_property(f"txpower_{setting}", ap.txpower)

    assert None not in powers.values(), f"iw shows no txpower: {powers}"
    assert powers["0"] <= powers["1"] < powers["auto"], (
        f"txpower 0 gave {powers['0']} dBm, 1 gave {powers['1']} dBm, "
        f"auto {powers['auto']} dBm"
    )


def test_wifi_country_applies(bed, record_property):
    """Regression: openwrt/openwrt#13070

    With two radios, changing `country` on both must change the global
    regulatory domain at `wifi reload` (checked without a reboot).
    """
    bed.use_wpad("basic")
    radios = bed.load(radios=2)
    assert len(radios) >= 2, f"needs two radios, got {radios}"
    seen = {}
    for country in ("DE", "US"):
        bed.set_radio(radios[0], 1, band="2g", htmode="HT20", country=country)
        bed.set_radio(radios[1], 36, band="5g", htmode="HT20", country=country)
        for i, radio in enumerate(radios[:2]):
            bed.add_iface(f"t_ap{i}", radio, ssid=f"hwsim-cc{i}")
        bed.apply()
        assert bed.wait_ap("hwsim-cc0") and bed.wait_ap("hwsim-cc1"), bed.debug()
        reg = bed.sh("iw reg get")[0]
        m = re.search(r"^global\s*\ncountry (\w\w):", reg, re.MULTILINE)
        seen[country] = m.group(1) if m else None
        record_property(f"regdomain_after_{country}", seen[country])
    assert seen == {"DE": "DE", "US": "US"}, f"set -> global regdomain: {seen}"


ACS_CASES = {
    "2g": {"channels": "1-6", "allowed": range(1, 7), "htmode": "HT20"},
    "5g": {"channels": "36-48", "allowed": range(36, 49), "htmode": "HT20"},
}


@pytest.mark.parametrize("band", ACS_CASES)
def test_wifi_acs_respects_channels(bed, band, record_property):
    """Regression: openwrt/openwrt#22774

    With `channel auto` and `channels` set, automatic channel selection
    picks a channel from that list, every time (5 restarts).
    """
    case = ACS_CASES[band]
    bed.use_wpad("basic")
    radio = bed.load()[0]
    bed.set_radio(
        radio, "auto", band=band, htmode=case["htmode"], channels=case["channels"]
    )
    bed.add_iface("t_ap", radio, ssid="hwsim-acs")
    bed.apply()
    picked = []
    for _ in range(5):
        ap = bed.wait_ap("hwsim-acs", timeout=90)
        assert ap, f"ACS AP did not come up\n{bed.debug()}"
        picked.append(ap.links[0][0])
        bed.sh("wifi down; sleep 2; wifi up")
        time.sleep(5)
    record_property("channels_picked", json.dumps(picked))
    wrong = [c for c in picked if c not in case["allowed"]]
    assert not wrong, f"channels {case['channels']}, ACS picked {picked}"


@pytest.mark.parametrize("bss", ["primary", "secondary"])
def test_wifi_multi_ssid_data(bed, bss, record_property):
    """Regression: openwrt/openwrt#24293 (the wifi-scripts/netifd part)

    Two SSIDs on one 2.4 GHz radio, both bridged to lan: a station on
    either BSS reaches the router. The bug was a secondary BSS that
    associates but forwards no data. (The mt7622 driver part needs S5.)
    """
    bed.use_wpad("basic")
    radios = bed.load(radios=2)
    dut, sta = radios[0], radios[1]
    bed.set_radio(dut, 1, band="2g", htmode="HT20")
    bed.add_iface("t_ap0", dut, ssid="hwsim-ssid0")
    bed.add_iface("t_ap1", dut, ssid="hwsim-ssid1")
    bed.apply()
    assert bed.wait_ap("hwsim-ssid0") and bed.wait_ap("hwsim-ssid1"), bed.debug()

    lan = bed.check("uci get network.lan.ipaddr").strip().split("/")[0]
    peer = bed.peer(sta)
    ssid = "hwsim-ssid0" if bss == "primary" else "hwsim-ssid1"
    assert peer.connect(ssid), f"station did not associate to {ssid}\n{bed.debug()}"
    peer.set_addr(re.sub(r"\.\d+$", ".200", lan) + "/24")
    received = peer.ping(lan, count=5)
    record_property("ping_received", received)
    assert received > 0, f"no data through the {bss} BSS {ssid}\n{bed.debug()}"


def test_wifi_vif_macs_unique(bed, record_property):
    """Regression: openwrt/openwrt#23578

    On a single-wiphy multi-radio device (hwsim multi_radio, the model of
    mt7996), the APs of all radios and a second AP on one radio get unique,
    unicast MAC addresses.
    """
    bed.use_wpad("basic")
    radios = bed.load(radios=1, multi_radio=True)
    by_band = {r.band: r for r in radios}
    settings = {"2g": (1, "psk2"), "5g": (36, "psk2"), "6g": (37, "sae")}
    ssids = []
    for band, (channel, encryption) in settings.items():
        if band not in by_band:
            continue
        bed.set_radio(by_band[band], channel, htmode="HE20")
        bed.add_iface(
            f"t_{band}", by_band[band], ssid=f"hwsim-{band}", encryption=encryption
        )
        ssids.append(f"hwsim-{band}")
    assert len(ssids) >= 2, f"multi_radio gave {radios}"
    bed.add_iface("t_2g_b", by_band.get("2g", radios[0]), ssid="hwsim-2g-b")
    ssids.append("hwsim-2g-b")
    bed.apply()

    aps = {ssid: bed.wait_ap(ssid) for ssid in ssids}
    assert all(aps.values()), f"not all APs up: {list(aps)}\n{bed.debug()}"
    macs = {ssid: ap.addr.lower() for ssid, ap in aps.items()}
    record_property("macs", json.dumps(macs))
    assert len(set(macs.values())) == len(macs), f"duplicate MACs: {macs}"
    for ssid, mac in macs.items():
        assert not int(mac.split(":")[0], 16) & 1, f"{ssid} has a multicast MAC {mac}"
        assert mac != "00:00:00:00:00:00", f"{ssid} has no MAC"


# --- full wpad: dynamic VLANs, MLO -------------------------------------------


def test_wifi_hwsim_dynamic_vlan_bridged(bed, record_property):
    """Regression: openwrt/openwrt#9944

    Per-station VLANs from a PSK (wifi-station/wifi-vlan, no RADIUS): a
    station with the VLAN's key lands in that VLAN's bridge and gets DHCP
    there. With `vlan_no_bridge` the VLAN interface was not bridged.
    """
    bed.use_wpad("full")
    radios = bed.load(radios=2)
    dut, sta = radios[0], radios[1]
    bed.uci(
        "set network.br_hwsim=device",
        "set network.br_hwsim.type=bridge",
        "set network.br_hwsim.name=br-hwsim",
        "set network.br_hwsim.bridge_empty=1",
        "set network.hwsimvlan=interface",
        "set network.hwsimvlan.device=br-hwsim",
        "set network.hwsimvlan.proto=static",
        "set network.hwsimvlan.ipaddr=192.168.150.1/24",
        "set dhcp.hwsimvlan=dhcp",
        "set dhcp.hwsimvlan.interface=hwsimvlan",
        "set dhcp.hwsimvlan.start=100",
        "set dhcp.hwsimvlan.limit=50",
        "set dhcp.hwsimvlan.leasetime=1h",
        "set dhcp.hwsimvlan.force=1",
        "add_list firewall.@zone[0].network=hwsimvlan",
    )
    bed.set_radio(dut, 1, band="2g", htmode="HT20")
    bed.add_iface("t_ap", dut, ssid="hwsim-dvlan", key="defaultkey1")
    bed.uci(
        "set wireless.t_vlan=wifi-vlan",
        "set wireless.t_vlan.iface=t_ap",
        "set wireless.t_vlan.name=v150",
        "set wireless.t_vlan.vid=150",
        "set wireless.t_vlan.network=hwsimvlan",
        "set wireless.t_sta=wifi-station",
        "set wireless.t_sta.iface=t_ap",
        "set wireless.t_sta.vid=150",
        f"set wireless.t_sta.key={KEY}",
    )
    bed.apply()
    assert bed.wait_ap("hwsim-dvlan"), bed.debug()

    peer = bed.peer(sta)
    assert peer.connect("hwsim-dvlan", key=KEY), f"VLAN key rejected\n{bed.debug()}"
    time.sleep(3)  # hostapd adds the VLAN iface on the station's association
    ports = bed.sh("ls /sys/class/net/br-hwsim/brif/ 2>/dev/null")[0].split()
    wireless_ports = [
        p for p in ports if bed.sh(f"[ -d /sys/class/net/{p}/phy80211 ]")[1] == 0
    ]
    record_property("bridge_ports", json.dumps(ports))
    record_property("bridge_link", bed.sh("bridge link 2>/dev/null")[0])
    lease = peer.dhcp()
    record_property("lease", lease)
    assert wireless_ports, (
        f"no VLAN wifi iface in br-hwsim (ports {ports})\n{bed.debug()}"
    )
    assert lease and lease.startswith("192.168.150."), f"DHCP in the VLAN gave {lease}"


def test_hostapd_survives_sta_reconnect_during_scan(bed, record_property):
    """Regression: openwrt/openwrt#25840

    Two APs and a station on the 5 GHz radio of a multi-radio wiphy; the
    station follows an external AP that changes channel, so hostapd moves
    (removes and re-adds) the second BSS while doing its HT40 scan. hostapd
    must not crash. This approximates the report (a STA reconnecting to an
    external AP) with an external AP in a netns.
    """
    bed.use_wpad("full")
    radios = bed.load(radios=2, multi_radio=True)
    paths = sorted({r.path for r in radios})
    assert len(paths) == 2, f"expected two wiphys: {radios}"
    dut = next(r for r in radios if r.path == paths[0] and r.band == "5g")
    ext = next(r for r in radios if r.path == paths[1])
    bed.uci("set network.hwsimsta=interface", "set network.hwsimsta.proto=none")
    bed.set_radio(dut, 36, htmode="HT40")
    bed.add_iface("t_ap0", dut, ssid="hwsim-scan0")
    bed.add_iface("t_ap1", dut, ssid="hwsim-scan1")
    bed.add_iface("t_sta", dut, mode="sta", ssid="hwsim-ext", network="hwsimsta")

    peer = bed.peer(ext)
    peer.start_ap("hwsim-ext", 36)
    bed.apply()
    assert bed.wait_ap("hwsim-scan1", timeout=90), bed.debug()
    pid = bed.hostapd_pid()
    segfaults = bed.sh("dmesg | grep -c segfault")[0].strip()
    token = bed.mark()

    reconnects = 0
    for channel in (44, 36, 44, 36, 44, 36):
        peer.start_ap("hwsim-ext", channel)
        if bed.wait(
            lambda ifs, c=channel: any(
                i.type == "managed" and i.links and i.links[0][0] == c for i in ifs
            ),
            timeout=45,
        ):
            reconnects += 1
        time.sleep(3)  # the APs follow the station

    log = bed.log_since(token)
    record_property("reconnects", reconnects)
    record_property("hostapd_pid_before", pid)
    record_property("hostapd_pid_after", bed.hostapd_pid())
    assert pid and bed.hostapd_pid() == pid, f"hostapd restarted\n{bed.debug()}"
    assert "segfault" not in log, log
    assert bed.sh("dmesg | grep -c segfault")[0].strip() == segfaults, (
        "segfault in dmesg"
    )
    assert reconnects, f"the station never followed the external AP\n{bed.debug()}"


def mlo_bed(bed):
    """One wiphy with 2.4/5/6 GHz radios and MLO: (2g radio, 5g radio)."""
    bed.use_wpad("full")
    radios = bed.load(radios=1, multi_radio=True, mlo=True)
    by_band = {r.band: r for r in radios}
    if "2g" not in by_band or "5g" not in by_band:
        pytest.fail(f"multi_radio hwsim without 2g and 5g radios: {radios}")
    return by_band["2g"], by_band["5g"]


def mld(ifs, ssid="hwsim-mld"):
    return next((i for i in ifs if i.ssid == ssid and i.type == "AP" and i.links), None)


def test_wifi_mlo_he_htmode(bed, record_property):
    """Regression: openwrt/openwrt#25414

    An AP MLD over 2.4 GHz (htmode HE20) and 5 GHz (EHT80): either the
    links come up, or wifi-scripts reports an error, and in no case does an
    interface come up on a band no radio of the MLD is configured for.
    """
    r2g, r5g = mlo_bed(bed)
    bed.set_radio(r2g, 1, htmode="HE20")
    bed.set_radio(r5g, 36, htmode="EHT80")
    bed.add_iface("t_mld", [r2g, r5g], ssid="hwsim-mld", encryption="sae", mlo="1")
    token = bed.mark()
    bed.apply()

    ap = bed.wait(mld, timeout=60)
    log = bed.log_since(token)
    mld_log = [
        line
        for line in log.splitlines()
        if re.search(r"mld|mlo|htmode", line, re.IGNORECASE)
    ]
    aps = [i for i in bed.ifaces() if i.ssid == "hwsim-mld"]
    bands = sorted({band_of(f) for i in aps for _, f, _ in i.links})
    record_property("links", json.dumps([i.links for i in aps]))
    record_property("mld_log", json.dumps(mld_log[-20:]))
    assert set(bands) <= {"2g", "5g"}, (
        f"an interface on a wrong band: {bands}\n{bed.debug()}"
    )
    assert ap or mld_log, f"MLD neither up nor an error logged\n{bed.debug()}"


def test_wifi_mlo_width_survives_other_radio_reload(bed, record_property):
    """Regression: openwrt/openwrt#25413

    An AP MLD with its 5 GHz link at 80 MHz keeps that width when only the
    2.4 GHz radio is reconfigured (channel change and `wifi reload`).
    80 MHz on 36-48 avoids the 60 s DFS wait that 160 MHz would need.
    """
    r2g, r5g = mlo_bed(bed)
    bed.set_radio(r2g, 1, htmode="EHT20")
    bed.set_radio(r5g, 36, htmode="EHT80")
    bed.add_iface("t_mld", [r2g, r5g], ssid="hwsim-mld", encryption="sae", mlo="1")
    bed.apply()

    def link(band):
        def find(ifs):
            ap = mld(ifs)
            return ap and next((lk for lk in ap.links if band_of(lk[1]) == band), None)

        return find

    before = bed.wait(link("5g"), timeout=90)
    assert before, f"no 5 GHz MLD link\n{bed.debug()}"
    record_property("width_before", before[2])
    assert before[2] == 80, f"5 GHz link starts at {before[2]} MHz\n{bed.debug()}"

    bed.uci(f"set wireless.{r2g.section}.channel=6")
    bed.apply()
    moved = bed.wait(lambda ifs: (lk := link("2g")(ifs)) and lk[0] == 6, timeout=60)
    assert moved, f"2.4 GHz link did not move to channel 6\n{bed.debug()}"
    after = bed.wait(link("5g"), timeout=30)
    record_property("width_after", after and after[2])
    assert after and after[2] == 80, f"5 GHz link after the 2.4 GHz reload: {after}"


# --- mesh wpad ------------------------------------------------------------


@pytest.mark.parametrize("channel", [6, 8, 11])
def test_wifi_mesh_he40_upper(bed, channel, record_property):
    """Regression: openwrt/openwrt#26335

    802.11s mesh plus an AP on one 2.4 GHz radio at HE40 joins and peers on
    every channel; channels 8-11 (secondary channel below) failed with
    "Could not select hw_mode and channel". Channel 6 is the control. The
    mesh peer is the second radio of the same VM.
    """
    bed.use_wpad("mesh")
    radios = bed.load(radios=2)
    dut, other = radios[0], radios[1]
    for radio in (dut, other):
        bed.set_radio(radio, channel, band="2g", htmode="HE40")
    bed.add_iface("t_ap", dut, ssid="hwsim-meshap")
    bed.add_iface("t_mesh", dut, mode="mesh", mesh_id="hwsim-mesh", encryption="sae")
    # not bridged: two bridged mesh ifaces of one host would loop
    bed.add_iface(
        "t_peer",
        other,
        mode="mesh",
        mesh_id="hwsim-mesh",
        encryption="sae",
        network=None,
    )
    token = bed.mark()
    bed.apply()

    meshes = bed.wait(
        lambda ifs: (
            [i for i in ifs if i.type == "mesh point" and i.links]
            if sum(i.type == "mesh point" and bool(i.links) for i in ifs) >= 2
            else None
        ),
        timeout=60,
    )
    log = bed.log_since(token)
    record_property("mesh_links", json.dumps([(i.name, i.links) for i in meshes or []]))
    assert "Could not select hw_mode and channel" not in log, log
    assert meshes, f"mesh ifaces not up on channel {channel}\n{bed.debug()}"
    assert all(i.links[0][0] == channel for i in meshes), [i.links for i in meshes]

    dut_mesh = next(i for i in meshes if i.phy == dut.phy)
    deadline = time.monotonic() + 45
    dump = ""
    while time.monotonic() < deadline:
        dump = bed.sh(f"iw dev {dut_mesh.name} station dump")[0]
        if re.search(r"mesh plink:\s+ESTAB", dump):
            break
        time.sleep(3)
    record_property("station_dump", dump)
    assert re.search(r"mesh plink:\s+ESTAB", dump), f"no mesh peering\n{bed.debug()}"
