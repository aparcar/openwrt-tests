"""System health monitoring tests for OpenWrt."""

import re


class TestSystemHealth:
    """Tests for monitoring system health and resource usage."""

    def test_cpu_load(self, ssh_command, results_bag):
        """Test CPU load is within acceptable limits."""
        # Get load average for 1, 5, and 15 minutes
        output = ssh_command.run_check("uptime")
        load_match = re.search(r"load average: ([\d.]+), ([\d.]+), ([\d.]+)", output[0])

        assert load_match, "Could not parse load average"

        load_1min = float(load_match.group(1))
        load_5min = float(load_match.group(2))
        load_15min = float(load_match.group(3))

        results_bag["cpu_load"] = {
            "1min": load_1min,
            "5min": load_5min,
            "15min": load_15min,
        }

        # Load should generally be less than 2x CPU count for healthy system
        assert load_15min < 1, f"15-minute load average {load_15min} is stragely high"

    def test_memory_usage(self, ssh_command, results_bag):
        """Test memory usage and check for memory leaks."""
        # Parse memory information
        mem_output = ssh_command.run_check("free -m")
        mem_lines = mem_output[1].split()

        total_mem = int(mem_lines[1])
        used_mem = int(mem_lines[2])
        free_mem = int(mem_lines[3])
        available_mem = int(mem_lines[6]) if len(mem_lines) > 6 else free_mem

        # Calculate percentage
        mem_percent = (used_mem / total_mem) * 100

        results_bag["memory_usage"] = {
            "total_mb": total_mem,
            "used_mb": used_mem,
            "free_mb": free_mem,
            "available_mb": available_mem,
            "percent_used": round(mem_percent, 2),
        }

        # Memory usage should be less than 90%
        assert mem_percent < 90, f"Memory usage {mem_percent:.1f}% is too high"

        # Should have at least 10MB available
        assert available_mem > 10, f"Only {available_mem}MB available memory"

    def test_filesystem_usage(self, ssh_command, results_bag):
        """Test filesystem usage on critical mount points."""
        # Check key filesystems
        filesystems = ["/", "/tmp", "/overlay"]
        fs_usage = {}

        for fs in filesystems:
            # Skip if filesystem doesn't exist
            if ssh_command.run(f"test -d {fs}")[2] != 0:
                continue

            df_output = ssh_command.run_check(f"df -h {fs} | tail -1")
            parts = df_output[0].split()

            if len(parts) >= 5:
                fs_usage[fs] = {
                    "filesystem": parts[0],
                    "size": parts[1],
                    "used": parts[2],
                    "available": parts[3],
                    "percent_used": int(parts[4].rstrip("%")),
                }

        results_bag["filesystem_usage"] = fs_usage

        # Check critical filesystems aren't full
        for fs, usage in fs_usage.items():
            assert usage["percent_used"] < 95, (
                f"Filesystem {fs} is {usage['percent_used']}% full"
            )

    def test_system_uptime(self, ssh_command, results_bag):
        """Test and record system uptime."""
        uptime_output = ssh_command.run_check("cat /proc/uptime")
        uptime_seconds = float(uptime_output[0].split()[0])

        assert uptime_seconds < 3600, "System uptime is over 1 hour"

    def test_temperature_sensors(self, ssh_command, results_bag):
        """Test temperature sensors if available."""
        # Check if thermal zones exist
        thermal_zones = ssh_command.run(
            "ls /sys/class/thermal/thermal_zone*/temp 2>/dev/null"
        )[0]

        if thermal_zones:
            temperatures = {}
            for zone in thermal_zones:
                if zone:
                    temp_raw = ssh_command.run_check(f"cat {zone}")[0]
                    temp_celsius = int(temp_raw) / 1000
                    zone_name = zone.split("/")[-2]
                    temperatures[zone_name] = temp_celsius

            results_bag["temperatures"] = temperatures

            # Check if any temperature is critically high (>85°C)
            for zone, temp in temperatures.items():
                assert temp < 85, f"Temperature in {zone} is critically high: {temp}°C"

    def test_process_count(self, ssh_command, results_bag):
        """Test number of running processes is reasonable."""
        proc_count = int(ssh_command.run_check("ps | wc -l")[0])

        results_bag["process_count"] = proc_count

        # Alert if too many processes (possible fork bomb or resource leak)
        assert proc_count < 300, f"Too many processes running: {proc_count}"

        # Alert if too few processes (system might not be fully functional)
        assert proc_count > 20, f"Too few processes running: {proc_count}"

    def test_zombie_processes(self, ssh_command):
        """Check for zombie processes."""
        zombies = ssh_command.run("ps | grep -E '\\s+Z\\s+' | grep -v grep")[0]

        zombie_count = len(zombies)

        assert zombie_count == 0, f"Found {zombie_count} zombie processes:\n{zombies}"

    def test_entropy_available(self, ssh_command):
        """Test that sufficient entropy is available for cryptographic operations."""
        entropy = int(
            ssh_command.run_check("cat /proc/sys/kernel/random/entropy_avail")[0]
        )

        # Should have at least 256 bits of entropy
        assert entropy >= 256, f"Insufficient entropy available: {entropy} bits"

    def test_open_file_descriptors(self, ssh_command, results_bag):
        """Test system-wide open file descriptors."""
        # Get current and max file descriptors
        fd_info = ssh_command.run_check("cat /proc/sys/fs/file-nr")[0].split()

        allocated_fds = int(fd_info[0])
        max_fds = int(fd_info[2])

        fd_percent = (allocated_fds / max_fds) * 100

        results_bag["file_descriptors"] = {
            "allocated": allocated_fds,
            "maximum": max_fds,
            "percent_used": round(fd_percent, 2),
        }

        # Should not be close to the limit
        assert fd_percent < 80, f"Too many file descriptors in use: {fd_percent:.1f}%"
