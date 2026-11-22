# Known upstream issues

Tests that reproduce open OpenWrt issues. Each is marked `xfail` (not
strict) with the issue in its reason: while the bug exists the result is
XFAIL, once it's fixed it turns XPASS, which is the signal to drop the mark.
Device-specific marks are added at run time, so other devices run the test
as a normal one.

| Test | Issue | Where | What fails |
|---|---|---|---|
| `test_known_issues_userspace.py::test_dnsmasq_survives_reloads` | [openwrt#25409](https://github.com/openwrt/openwrt/issues/25409) | any device, QEMU | `dnsmasq reload` with a changed config kills the jailed instance (SIGHUP races ujail); procd gives up after 5 crashes and DHCP/DNS stay down |
| `test_known_issues_userspace.py::test_dnsmasq_dhcp_range_without_dhcpv4` | [openwrt#25067](https://github.com/openwrt/openwrt/issues/25067) | any device, QEMU | a `config dhcp` section without `dhcpv4 'server'` silently gets no `dhcp-range` |
| `test_known_issues_userspace.py::test_firewall_stop_keeps_foreign_tables` | [openwrt#11620](https://github.com/openwrt/openwrt/issues/11620) | firewall4 images, QEMU | `service firewall stop` runs `fw4 flush` and deletes other packages' nft tables |
| `test_known_issues_userspace.py::test_wireguard_hex_mtu_and_port_name` | [openwrt#26538](https://github.com/openwrt/openwrt/issues/26538) | main (ucode wireguard handler) | a hex `mtu` (0x578) and a service-name `listen_port` (openvpn) are not applied |
| `test_wifi_hwsim.py::test_wifi_reload_applies_wifi_vlan` | [openwrt#26811](https://github.com/openwrt/openwrt/issues/26811) | QEMU hwsim | `wifi reload` doesn't apply wifi-vlan changes (`bridge_isolate`) |
| `test_wifi_hwsim.py::test_wifi_multi_radio_sta_stays_on_band` | [openwrt#25662](https://github.com/openwrt/openwrt/issues/25662) | QEMU hwsim (multi_radio) | a 2.4 GHz station on a multi-radio wiphy joins its SSID on 5 GHz |
| `test_known_issues_net.py::test_bridge_mac_returns_from_foreign_port` | [openwrt#26131](https://github.com/openwrt/openwrt/issues/26131) (+#24760, #25305) | MT753x DSA: BPi-R4-Lite, BPi-R4, EX400 | a host is unreachable for minutes after its MAC was learned on a non-switch bridge port |
| `test_known_issues_net.py::test_ra_prefix_survives_upstream_reconnect` | [odhcpd#387](https://github.com/openwrt/odhcpd/issues/387) | any probe DUT (simulated upstream) | after an upstream reconnect with `ip6ifaceid random`, the LAN /64 is announced with lifetime 0 |
| `test_known_issues_net.py::test_flowtable_keeps_wifi_after_restart[band]` | [openwrt#25646](https://github.com/openwrt/openwrt/issues/25646) | any device with Wi-Fi | the AP interface is missing from the flowtable after `wifi down/up` |
| `test_probe_lan.py::test_lan_soak_no_tx_timeout` | [openwrt#20570](https://github.com/openwrt/openwrt/issues/20570) | rpi-4 | NETDEV WATCHDOG tx timeout during the LAN iperf soak |
| `test_known_issues_platform.py::test_poweroff` | [openwrt#18481](https://github.com/openwrt/openwrt/issues/18481) | openwrt_one | the board boots again ~25 s after `poweroff` (EM6324 watchdog) |
| `test_known_issues_platform.py::test_reboot` | [openwrt#26753](https://github.com/openwrt/openwrt/issues/26753) | ignitenet_ss-w2-ac2600, snapshot | `reboot` hangs: no bootloader banner after "Restarting system" |
| `test_known_issues_platform.py::test_qcom_socinfo_debugfs` | [openwrt#17756](https://github.com/openwrt/openwrt/issues/17756) | ipq806x | reading `/sys/kernel/debug/qcom_socinfo/*` oopses |
| – (no session to test in) | [openwrt#25302](https://github.com/openwrt/openwrt/issues/25302) | enterasys_ws-ap3710i, 25.12 | initramfs hangs after "Uncompressing Kernel Image"; healthcheck pinned to 23.05.5 |

Notes:

- `test_reboot` and `test_poweroff` run on every board whose strategy has an
  `off` state (not QEMU, not SD-mux) and power it off afterwards, so the
  next test boots it again.
- odhcpd#387 uses a static interface on `lo` as a stand-in upstream; move it
  to the debian probe's DHCPv6-PD WAN once that's available, since the
  stand-in may not trigger the bug.
- The dnsmasq, firewall and #26131 tests briefly disrupt DHCP/DNS, the
  firewall or the probe's LAN link (up to ~5 minutes on an affected board).
