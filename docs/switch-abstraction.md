# Switch abstraction - dynamic VLAN switching

Optional integration with the `switch-vlan` CLI from
[labgrid-switch-abstraction](https://github.com/fcefyn-testbed/labgrid-switch-abstraction)
for dynamic per-port VLAN management during multi-node tests.

When enabled, the `shared_vlan_multi` fixture (session-scoped) reconfigures
the managed switch before the test (moving all listed DUTs to a shared VLAN)
and restores the original topology on teardown. This is **opt-in**: labs
without a managed switch can ignore it entirely.

## Where `switch-vlan` runs

The fixture invokes the `switch-vlan` CLI via subprocess. Two execution modes:

| `LG_PROXY` | Command runs on            | What you need locally                                                  |
|------------|----------------------------|------------------------------------------------------------------------|
| Set        | The proxy/lab host via SSH | Just SSH access (no switch credentials, no `switch-vlan` install)      |
| Unset      | Current host               | `switch-vlan` in PATH and a configured `switch.conf` / `dut-config.yaml` |

Remote developers therefore **do not need switch credentials** or any
switch-related install: the lab host owns the credentials, and the fixture
tunnels the command over SSH transparently.

## Setup (lab host only)

1. Run the `playbook_labgrid.yml` playbook. The `switch-vlan` CLI is
   installed via pipx alongside `labgrid` and `usbsdmux`; no manual pip
   step is required. Labs without a managed switch can ignore the next
   step and the CLI will simply stay unused.

2. Configure `~/.config/switch.conf` (or `/etc/switch.conf` for multi-user
   labs) with the switch credentials, and `dut-config.yaml` with the
   DUT-to-port map. See the
   [labgrid-switch-abstraction README](https://github.com/fcefyn-testbed/labgrid-switch-abstraction#readme)
   for the full reference.

3. Verify on the lab host:

   ```bash
   switch-vlan --help
   ```

## Setup (test runner)

1. Enable VLAN switching:

   ```bash
   export VLAN_SWITCH_ENABLED=1
   ```

   Without this variable (or set to `0`), the fixture is a no-op.

2. List the DUTs participating in the multi-node test:

   ```bash
   export LG_MULTI_PLACES="labgrid-mylab-router_1,labgrid-mylab-router_2"
   ```

3. (Remote runs only) Set `LG_PROXY` to reach the lab host:

   ```bash
   export LG_PROXY=labgrid-mylab
   ```

That is all. The fixture handles the rest.

## Optional configuration

| Env var          | Purpose                                                  | Default                              |
|------------------|----------------------------------------------------------|--------------------------------------|
| `VLAN_SHARED`    | Target VLAN ID for the multi-node test                   | 200                                  |
| `PLACE_PREFIX`   | Prefix to strip from place names to derive DUT names     | unset (strip up to second hyphen)    |

## Place name mapping

DUT names passed to `switch-vlan` are derived from labgrid place names. By
default, everything up to and including the second hyphen is stripped:

```
labgrid-mylab-router_1  ->  router_1
```

Override with `PLACE_PREFIX` if your lab uses a different convention:

```bash
export PLACE_PREFIX="labgrid-mylab-"
```
