# Self-Hosted Runner Management

This document describes how to use the self-hosted runner management workflow to automatically provision, run tests, and clean up GitHub Actions runners on specific hosts.

## Overview

The `self-hosted-runner.yml` workflow automates the process of:

1. **Provisioning** self-hosted runners on a specified lab host
2. **Running** the build/test matrix using those runners
3. **Cleaning up** the runners after completion (or allowing them to self-remove if configured as ephemeral)

## Usage

### Triggering the Workflow

The workflow can be triggered in two ways:

1. **Manual Trigger** (workflow_dispatch):
   - Go to the Actions tab in GitHub
   - Select "Self-Hosted Runner Matrix Tests"
   - Click "Run workflow"
   - Select the host and number of runners
   - Click "Run workflow"

2. **Scheduled Trigger**:
   - Runs automatically every Monday at 2 AM UTC

### Workflow Inputs

- **host**: The lab name where runners should be deployed (e.g., `labgrid-aparcar`, `labgrid-hsn`)
- **runner_count**: Number of parallel runners to spawn (default: 2)

## How It Works

### 1. Generate Test Matrix

The workflow first generates a test matrix based on the selected host and its available devices from `labnet.yaml`:

```yaml
matrix:
  include:
    - device: openwrt_one
      name: OpenWrt One
      proxy: labgrid-aparcar
      target: mediatek-filogic
      firmware: initramfs.itb
    - device: tplink_tl-wdr3600-v1
      name: TP-Link TL-WDR3600 v1
      proxy: labgrid-aparcar
      target: ath79-generic
      firmware: initramfs-kernel.bin
```

### 2. Setup Runners

The workflow generates a setup script that can be executed on the target host to:

- Download the GitHub Actions runner package (if not already present)
- Configure multiple runners with unique names and a shared label
- Start runners as ephemeral (automatically remove after one job)
- Run runners in the background

The generated script is available as a workflow artifact: `runner-setup-script-<run_id>`

### 3. Run Tests (Optional)

The test matrix job is currently disabled by default (`if: false`). When enabled, it will:

- Run tests for each device in the matrix
- Use the dynamically provisioned self-hosted runners
- Download firmware from OpenWrt mirror
- Execute pytest tests with labgrid
- Upload test results as artifacts

### 4. Cleanup Runners

A cleanup script is generated to:

- Stop all runners associated with this workflow run
- Remove runner configurations
- Clean up runner directories

The cleanup script is available as a workflow artifact: `runner-cleanup-script-<run_id>`

## Manual Runner Setup

To manually set up runners on a host:

1. Download the setup script from the workflow artifacts

2. Get a runner registration token from the workflow output (or generate one using GitHub API)

3. Execute the script on the target host:

```bash
bash setup-runners.sh "<RUN_ID>" "<RUNNER_COUNT>" "<RUNNER_TOKEN>" "https://github.com/aparcar/openwrt-tests"
```

Example:
```bash
bash setup-runners.sh "12345678" "2" "ABCDEF123456..." "https://github.com/aparcar/openwrt-tests"
```

This will:
- Create `~/github-runners/runner-<RUN_ID>-1/` and `~/github-runners/runner-<RUN_ID>-2/`
- Configure runners with label `runner-<RUN_ID>`
- Start runners in the background

## Manual Runner Cleanup

To manually clean up runners:

1. Download the cleanup script from the workflow artifacts

2. Get a runner removal token from the workflow output (or generate one using GitHub API)

3. Execute the script on the target host:

```bash
bash cleanup-runners.sh "<RUN_ID>" "<REMOVAL_TOKEN>"
```

Example:
```bash
bash cleanup-runners.sh "12345678" "GHIJK789012..."
```

## Ephemeral Runners

Runners are configured as **ephemeral** by default, meaning they:

- Automatically remove themselves after completing **one job**
- Don't require manual cleanup in most cases
- Ensure a clean state for each workflow run

If a runner fails to self-remove (e.g., due to a crash), use the cleanup script to remove it manually.

## Runner Labels

Each workflow run creates runners with a unique label:

```
runner-<WORKFLOW_RUN_ID>
```

For example, if the workflow run ID is `12345678`, runners will have the label `runner-12345678`.

This ensures:
- Runners are isolated per workflow run
- No conflicts between concurrent runs
- Easy identification and cleanup

## Integration with Existing Infrastructure

This workflow is designed to complement the existing labgrid infrastructure:

- Uses the same `labnet.yaml` configuration
- Respects lab device assignments
- Works with labgrid-client for device management
- Can coexist with the `global-coordinator` runner

## Troubleshooting

### Runners Not Appearing

If runners don't appear in the GitHub UI:

1. Check the runner setup script execution logs
2. Verify network connectivity to GitHub
3. Ensure the registration token hasn't expired (tokens are valid for 1 hour)
4. Check the runner log file: `~/github-runners/runner-<RUN_ID>-<N>/runner.log`

### Runners Not Cleaning Up

If ephemeral runners don't self-remove:

1. Use the cleanup script to manually remove them
2. Check the runner process: `ps aux | grep runner`
3. Manually kill stuck processes if needed
4. Remove runner directories: `rm -rf ~/github-runners/runner-<RUN_ID>-*`

### Runner Configuration Errors

If runner configuration fails:

1. Check that the host has internet access to `github.com`
2. Verify the runner package downloaded correctly
3. Ensure the token is valid and has correct permissions
4. Check disk space: `df -h ~/github-runners`

## Security Considerations

- **Tokens**: Registration and removal tokens are short-lived (1 hour) and masked in logs
- **Ephemeral Runners**: Using ephemeral runners ensures a clean state for each run
- **Isolation**: Each workflow run uses uniquely labeled runners
- **Access Control**: Runners should only be deployed on trusted hosts within the lab network

## Future Enhancements

Potential improvements to this workflow:

1. **Automated SSH Execution**: Automatically execute setup/cleanup scripts via SSH
2. **Runner Health Monitoring**: Monitor runner status and auto-restart failed runners
3. **Dynamic Scaling**: Adjust runner count based on matrix size
4. **Runner Pooling**: Maintain a pool of warm runners for faster job startup
5. **Integration with Ansible**: Use Ansible playbooks for runner management

## References

- [GitHub Actions Self-Hosted Runners](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners)
- [Ephemeral Runners](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/autoscaling-with-self-hosted-runners#using-ephemeral-runners-for-autoscaling)
- [Runner API](https://docs.github.com/en/rest/actions/self-hosted-runners)
