# OpenWRT Comprehensive Test Suite

This directory contains a comprehensive test suite for OpenWRT systems. These tests validate system health, performance, security, and functionality.

## Test Categories

### 1. System Health Tests (`test_system_health.py`)

Tests for monitoring overall system health and resource usage.

- **CPU Load Testing**: Validates CPU load is within acceptable limits
- **Memory Usage**: Checks for memory leaks and excessive usage
- **Filesystem Usage**: Monitors disk space on critical mount points
- **System Uptime**: Records and validates system uptime
- **Temperature Monitoring**: Checks thermal sensors if available
- **Kernel Error Detection**: Scans kernel logs for critical errors
- **Process Management**: Validates process counts and checks for zombies
- **Swap Usage**: Monitors swap usage if configured
- **Entropy Availability**: Ensures sufficient entropy for crypto operations
- **Time Synchronization**: Validates system time and NTP configuration
- **File Descriptors**: Monitors system-wide file descriptor usage
- **Memory Pressure Testing**: Tests system behavior under memory stress

### 2. UCI Configuration Tests (`test_uci.py`)

Tests for OpenWRT's Unified Configuration Interface.

- **Basic UCI Operations**: Tests get, set, delete commands
- **List Operations**: Validates UCI list handling
- **Revert Functionality**: Tests configuration rollback
- **Export/Import**: Validates configuration backup/restore
- **Batch Mode**: Tests batch configuration changes
- **Configuration Validation**: Validates system configurations
- **Change Tracking**: Tests UCI change detection
- **System Configuration**: Validates hostname, timezone settings
- **Permission Checks**: Verifies configuration file permissions
- **Stress Testing**: Tests UCI under heavy load

### 3. Firewall and Security Tests (`test_firewall.py`)

Comprehensive firewall and security validation.

- **Service Status**: Validates firewall daemon is running
- **Zone Configuration**: Tests firewall zones (LAN/WAN)
- **Rule Validation**: Checks iptables/nftables rules
- **Port Forwarding**: Tests redirect rules
- **Custom Rules**: Validates custom firewall scripts
- **Logging Configuration**: Tests firewall logging
- **Default Policies**: Validates secure default policies
- **DDoS Protection**: Tests SYN flood protection
- **Packet Filtering**: Tests invalid packet handling
- **Zone Forwarding**: Validates inter-zone forwarding
- **Connection Tracking**: Tests conntrack functionality
- **MAC Filtering**: Tests MAC address filtering
- **IPv6 Firewall**: Validates IPv6 firewall rules

### 4. System Services Tests (`test_services.py`)

Tests for system daemons and services.

- **Init System**: Validates procd functionality
- **Essential Services**: Tests core services (SSH, DNS, DHCP)
- **Service Scripts**: Validates init.d scripts
- **Boot Services**: Tests enabled services
- **Service Management**: Tests start/stop/restart
- **Cron Daemon**: Validates scheduled task execution
- **System Logging**: Tests logd functionality
- **DNS/DHCP Server**: Tests dnsmasq service
- **Web Server**: Validates uhttpd operation
- **NTP Service**: Tests time synchronization
- **Service Dependencies**: Validates boot order
- **Watchdog**: Tests hardware watchdog if available
- **Message Bus**: Tests ubus functionality

### 5. Performance Tests (`test_performance.py`)

System performance benchmarks and stress tests.

- **Boot Time**: Measures system boot performance
- **Memory Bandwidth**: Tests RAM read/write speeds
- **CPU Performance**: Basic CPU benchmarks
- **Network Throughput**: Loopback throughput testing
- **Filesystem Performance**: Tests I/O on different filesystems
- **Process Creation**: Measures fork/exec performance
- **Interrupt Handling**: Validates interrupt processing
- **Sustained Load**: Tests stability under load
- **Network Latency**: Measures latency and jitter
- **Concurrent Connections**: Tests connection handling
- **Memory Fragmentation**: Tests memory allocation
- **Cache Performance**: Validates filesystem caching

## Running the Tests

### Prerequisites

- Python 3.8+
- pytest
- SSH access to OpenWRT device
- Required Python packages (see pyproject.toml)

### Basic Usage

Run all tests:

```bash
pytest tests/
```

Run specific test category:

```bash
pytest tests/test_system_health.py
```

Run with specific markers:

```bash
# Run only slow tests
pytest -m slow

# Skip slow tests
pytest -m "not slow"
```

### Environment Variables

- `LG_ENV`: Device environment configuration
- `FIRMWARE_VERSION`: Expected firmware version for validation

### Test Results

Results are saved to:

- `results.json`: Machine-readable test results

## Test Markers

- `@pytest.mark.slow`: Long-running tests (>30 seconds)
- `@pytest.mark.lg_feature`: Feature-specific tests

## Adding New Tests

1. Create test functions starting with `test_`
2. Use appropriate fixtures:
   - `ssh_command`: For SSH command execution
   - `shell_command`: For shell access
   - `results_bag`: To store test results
3. Add assertions to validate expected behavior
4. Use meaningful test names and docstrings

## Best Practices

1. **Cleanup**: Always clean up test artifacts
2. **Idempotency**: Tests should be runnable multiple times
3. **Independence**: Tests should not depend on each other
4. **Safety**: Avoid operations that could break the system
5. **Documentation**: Add clear docstrings to all tests

## Common Test Patterns

### Running Commands

```python
def test_example(ssh_command):
    # Simple command
    output = ssh_command.run_check("uname -a")

    # Command with error handling
    result = ssh_command.run("some_command")
    if result[2] == 0:  # Check exit code
        process_output(result[0])
```

### UCI Operations

```python
def test_uci_example(ssh_command):
    # Get UCI value
    value = ssh_command.run("uci get system.@system[0].hostname")[0].strip()

    # Set UCI value
    ssh_command.run_check("uci set test.option='value'")
    ssh_command.run_check("uci commit")
```

### Performance Measurements

```python
def test_performance_example(ssh_command, results_bag):
    start_time = time.time()
    ssh_command.run_check("some_operation")
    duration = time.time() - start_time

    results_bag["operation_time"] = duration
    assert duration < 5.0, f"Operation too slow: {duration}s"
```

## Troubleshooting

### SSH Connection Issues

- Verify SSH keys are configured
- Check device IP and port
- Ensure dropbear is running on device

### Test Failures

- Check device logs: `logread | tail -50`
- Verify device configuration
- Check available resources (memory, disk)

### Performance Issues

- Reduce concurrent test execution
- Skip slow tests for quick validation
- Check device load during tests

## Contributing

When adding new tests:

1. Follow existing patterns and conventions
2. Add appropriate documentation
3. Test on multiple OpenWRT versions/devices
4. Consider resource constraints of embedded devices
5. Add cleanup code for all test artifacts
