# Daily Workflow for OpenWrt Testing

## Overview

The daily workflow (`.github/workflows/daily.yml`) runs comprehensive tests on both snapshot builds and stable release daily rebuilds. This workflow was evolved from the previous `snapshots.yml` to provide broader coverage across different OpenWrt versions.

## Workflow Triggers

The workflow runs:
- Daily at midnight UTC (`0 0 * * *`)
- On pushes to the `main` branch
- Manually via `workflow_dispatch`

## Version Support

The workflow automatically fetches and tests three types of OpenWrt builds:

### 1. Snapshot Builds
- **URL**: `https://mirror-03.infra.openwrt.org/snapshots/targets`
- **Description**: Latest development builds
- **Version**: `SNAPSHOT`

### 2. Stable Release Daily Rebuilds
- **URL**: `https://mirror-03.infra.openwrt.org/releases/{branch}-SNAPSHOT/targets`
- **Description**: Daily rebuilds of stable branches
- **Branches**: Automatically detected from `.versions.json`:
  - Current stable (e.g., `24.10-SNAPSHOT`)
  - Previous stable (e.g., `23.05-SNAPSHOT`)

## Version Detection

The workflow fetches version information from `https://downloads.openwrt.org/.versions.json`:

```json
{
  "stable_version": "24.10.2",
  "oldstable_version": "23.05.5"
}
```

Branch names are derived by removing the patch version:
- `24.10.2` → `24.10-SNAPSHOT`
- `23.05.5` → `23.05-SNAPSHOT`

## Firmware Resolution

### Snapshots
Uses the traditional approach with predictable filenames:
```
openwrt-{target}-{device}-{firmware}
```

### Stable Releases
Uses dynamic firmware resolution:

1. **Fetch profiles.json**: Downloads `targets/{target}/profiles.json`
2. **Extract real filename**: Searches the `images` array for the correct image type
3. **Fallback**: Uses constructed filename if dynamic resolution fails

Example profiles.json structure:
```json
{
  "profiles": {
    "generic": {
      "image_prefix": "openwrt-24.10-snapshot-r28784-155eea44e7-x86-64-generic",
      "images": [
        {
          "name": "openwrt-24.10-snapshot-r28784-155eea44e7-x86-64-generic-squashfs-combined.img.gz",
          "type": "combined",
          "filesystem": "squashfs"
        }
      ]
    }
  }
}
```

## Test Matrix

The workflow creates a comprehensive test matrix by combining:

### Real Hardware Tests
- All devices from `labnet.yaml`
- Filtered to exclude devices with open healthcheck issues
- Cross-multiplied with all supported versions
- Results in format: `Device {device} ({version})`

### QEMU Tests
- Three target architectures:
  - `malta-be` (MIPS big-endian)
  - `x86-64` (x86 64-bit)
  - `armsr-armv8` (ARM 64-bit)
- Cross-multiplied with all supported versions
- Results in format: `QEMU {target} ({version})`

## Artifact Organization

Test results are organized by device and version:
- Real hardware: `results-{device}-{version}`
- QEMU: `results-qemu_{target}-{version}`

## Dashboard Integration

The results page combines:
- Device matrix from `labnet.yaml`
- QEMU target definitions
- Version information for each test run
- Links to detailed test results

## Error Handling

The workflow includes several error handling mechanisms:

1. **URL Validation**: Tests accessibility of profiles.json before proceeding
2. **Firmware Fallback**: Falls back to constructed filenames if dynamic resolution fails
3. **Device Filtering**: Excludes devices with known health issues
4. **Graceful Degradation**: Continues testing other combinations if one fails

## Example Test Run

For a device `tplink_archer-c7-v2` with target `ath79-generic`, the workflow will:

1. **Snapshot**: Test with latest development build
2. **Stable (24.10)**: Test with 24.10-SNAPSHOT daily rebuild
3. **Old Stable (23.05)**: Test with 23.05-SNAPSHOT daily rebuild

Each test produces separate results and artifacts, allowing comparison across versions.

## Migration Notes

This workflow replaces the previous `snapshots.yml` workflow. Key changes:

- **Filename**: `snapshots.yml` → `daily.yml`
- **Scope**: Snapshots only → Snapshots + stable releases
- **Matrix**: Single version → Multi-version testing
- **Artifacts**: Per-device → Per-device-per-version
- **Firmware Resolution**: Static → Dynamic for stable releases

## Configuration

The workflow uses these key environment variables:

- `PYTHONUNBUFFERED="1"`: Ensure real-time log output
- `PYTEST_ADDOPTS="--color=yes"`: Colorized test output
- `LG_CONSOLE="internal"`: Use internal console handling
- `LG_FEATURE_APK="true"`: Enable APK package manager features
- `LG_FEATURE_ONLINE="true"`: Enable online features for QEMU tests

No manual configuration is required - all versions and URLs are auto-detected.
