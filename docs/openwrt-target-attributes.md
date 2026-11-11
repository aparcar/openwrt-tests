# OpenWrt Target Attributes

Target files can contain a top-level `openwrt:` section with metadata used by
openwrt-tests workflows, scripts, and tests

This metadata is specific to openwrt-tests and is separate from the labgrid
configuration under `targets:`

For example:

~~~yaml
openwrt:
  name: OpenWrt One
  target: mediatek-filogic
  profile: openwrt_one
  snapshots_only: false
  healthcheck_version: "23.05.5"
  lan_ipv4: "192.168.77.1/24" # optional, defaults to 192.168.1.1/24
  image:
    type: kernel
    filesystem: squashfs
~~~

## Attributes

### `name`

Human-readable device name used when displaying the target in CI

### `target`

OpenWrt target and subtarget used to locate firmware metadata and downloads

For example, `mediatek-filogic` maps to `mediatek/filogic`

### `profile`

Profile name used to select the device from OpenWrt's `profiles.json`

### `snapshots_only`

Optional boolean, defaults to `false`

When set to `true`, the target is tested only with snapshot builds instead of
stable release builds

### `healthcheck_version`

Optional OpenWrt release used specifically by the healthcheck

An explicit `RELEASE` environment variable takes precedence over this value
If neither is set, the healthcheck uses its default release

### `boot_timeout`

Optional time in seconds for booting the DUT into its shell, the first time a
test needs it (`shell_command` in `tests/conftest.py`)

Without it, the boot counts against the first test's per-test timeout (300 s,
`pyproject.toml`). Set it for targets whose boot takes longer, e.g. because the
strategy flashes the image first (`asus_rt-n16`: 900)

### `lan_ipv4`

Optional IPv4 address and prefix expected on `br-lan`

If omitted, it defaults to `192.168.1.1/24`

Example:

~~~yaml
openwrt:
  lan_ipv4: "192.168.77.1/24"
~~~

### `image.type`

Optional image type used when selecting firmware from `profiles.json`

Defaults to `kernel`
Some targets may require another type, such as `factory` or `combined`

### `image.filesystem`

Optional filesystem used together with `image.type` when selecting firmware
from `profiles.json`

If omitted, the image is selected by type without filtering by filesystem
