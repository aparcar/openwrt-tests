# Sharing Target Files Across Multiple Devices

When a lab has multiple physical devices of the same model (e.g., three Belkin RT3200 routers), creating separate target configuration files for each device leads to unnecessary duplication and maintenance overhead.

If this is your situation, you can use the `device_instances` field in `labnet.yaml` to define multiple physical instances sharing a single target file:

```yaml
labs:
  labgrid-example:
    proxy: labgrid-example
    hostkey: ssh-ed25519 AAAA...
    maintainers:
      - "@maintainer"
    devices:
      - linksys_e8450
    device_instances:
      linksys_e8450:
        - router_1
        - router_2
        - router_3
```

Device metadata (name, OpenWrt target, profile, image selection) lives in the labgrid target file (`targets/linksys_e8450.yaml`):

```yaml
openwrt:
  name: Linksys E8450 / Belkin RT3200
  target: mediatek-mt7622
  profile: linksys_e8450
  # image.type defaults to "kernel"; override only for factory / combined images
```

This will create three different labgrid places from the same configuration file.
- `labgrid-example-router_1` → `targets/linksys_e8450.yaml`
- `labgrid-example-router_2` → `targets/linksys_e8450.yaml`
- `labgrid-example-router_3` → `targets/linksys_e8450.yaml`

## Configuration Separation

- **Generic configuration** (drivers, prompts, strategies) → `targets/linksys_e8450.yaml` (shared)
- **Device-specific details** (serial ports, IPs, power) → `ansible/files/exporter/<lab>/exporter.yaml` (per device)

## Automatic Target Resolution

When running tests with pytest, simply set `LG_PLACE` - the target file is resolved automatically:

```bash
export LG_PLACE=labgrid-example-router_1
export LG_PROXY=labgrid-example

# LG_ENV is automatically set to targets/linksys_e8450.yaml
pytest tests/ --lg-log
```

## When to Use

✅ Use `device_instances` when:
- You have multiple devices of the same model
- Devices require identical driver configurations
- Only device-specific details (serial, IP, power) differ

❌ Don't use when:
- Devices need different driver configurations
- Firmware boot process differs between instances
- You only have one device of that specific model
