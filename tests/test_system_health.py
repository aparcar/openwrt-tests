"""The device's resources after boot: memory, filesystem space, uptime,
temperatures, processes, entropy and open files. They catch firmware that
leaves too little room to run, and the recorded values show how resource use
changes between releases."""


class TestSystemHealth:
    """Tests for monitoring system health and resource usage."""

    def test_memory_usage(self, ssh_command, record_property):
        """At most 95% of RAM is in use after boot; records the memory figures.

        Devices with little RAM that are this full already run out of memory
        under load, and the recorded values show memory use growing between
        releases.

        Reads /proc/meminfo instead of `free`: busybox ignores `-m` and its
        "used" leaves out tmpfs. Used is MemTotal - MemAvailable, so only
        reclaimable memory doesn't count.
        """
        meminfo = {}
        for line in ssh_command.run_check("cat /proc/meminfo"):
            key, value = line.split(":", 1)
            meminfo[key] = int(value.split()[0]) // 1024  # KiB -> MiB

        total_mem = meminfo["MemTotal"]
        free_mem = meminfo["MemFree"]
        available_mem = meminfo["MemAvailable"]
        used_mem = total_mem - available_mem
        mem_percent = (used_mem / total_mem) * 100

        record_property("memory_total_mb", total_mem)
        record_property("memory_used_mb", used_mem)
        record_property("memory_free_mb", free_mem)
        record_property("memory_available_mb", available_mem)
        record_property("memory_used_percent", round(mem_percent, 2))

        assert mem_percent <= 95, f"Memory usage {mem_percent:.1f}% is too high"

    def test_filesystem_usage(self, ssh_command, record_property):
        """The root filesystem, /tmp and /overlay are less than 95% full;
        records their size and usage.

        A full overlay means users can't save settings or install packages,
        and a full /tmp (RAM) breaks logging, downloads and upgrades.
        """
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

        for fs, usage in fs_usage.items():
            name = fs.strip("/") or "root"
            for key, value in usage.items():
                record_property(f"fs_{name}_{key}", value)

        # Check critical filesystems aren't full
        for fs, usage in fs_usage.items():
            assert usage["percent_used"] < 95, (
                f"Filesystem {fs} is {usage['percent_used']}% full"
            )

    def test_system_uptime(self, ssh_command):
        """The device has been up for less than an hour, i.e. it really booted
        the firmware for this run.

        A longer uptime means the tests ran on a system that was already
        running before, not a fresh boot of the image under test.
        """
        uptime_output = ssh_command.run_check("cat /proc/uptime")
        uptime_seconds = float(uptime_output[0].split()[0])

        assert uptime_seconds < 3600, "System uptime is over 1 hour"

    def test_temperature_sensors(self, ssh_command, record_property):
        """Every thermal sensor reads below 85 °C; records the temperatures.

        Overheating points to broken thermal or fan control and can damage
        the hardware or make it unstable. Devices without sensors pass.
        """
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

            for zone, temp in temperatures.items():
                record_property(f"temperature_{zone}_c", temp)

            # Check if any temperature is critically high (>85°C)
            for zone, temp in temperatures.items():
                assert temp < 85, f"Temperature in {zone} is critically high: {temp}°C"

    def test_process_count(self, ssh_command, record_property):
        """The number of running processes is plausible (between 20 and 300);
        records the count.

        Too many point to a process leak or respawn loop, too few to services
        that didn't start.
        """
        proc_count = int(ssh_command.run_check("ps | wc -l")[0])

        record_property("process_count", proc_count)

        # Alert if too many processes (possible fork bomb or resource leak)
        assert proc_count < 300, f"Too many processes running: {proc_count}"

        # Alert if too few processes (system might not be fully functional)
        assert proc_count > 20, f"Too few processes running: {proc_count}"

    def test_entropy_available(self, ssh_command):
        """The kernel's random number generator reports at least 256 bits of
        entropy.

        Without enough entropy, generating SSH host keys, Wi-Fi keys and TLS
        sessions can stall or be weaker.
        """
        entropy = int(
            ssh_command.run_check("cat /proc/sys/kernel/random/entropy_avail")[0]
        )

        # Should have at least 256 bits of entropy
        assert entropy >= 256, f"Insufficient entropy available: {entropy} bits"

    def test_open_file_descriptors(self, ssh_command, record_property):
        """Less than 80% of the system-wide file descriptor limit is in use;
        records the counts.

        Running out of file descriptors makes services fail to open files or
        sockets; a high count after boot points to a leak.
        """
        # Get current and max file descriptors
        fd_info = ssh_command.run_check("cat /proc/sys/fs/file-nr")[0].split()

        allocated_fds = int(fd_info[0])
        max_fds = int(fd_info[2])

        fd_percent = (allocated_fds / max_fds) * 100

        record_property("fd_allocated", allocated_fds)
        record_property("fd_maximum", max_fds)
        record_property("fd_used_percent", round(fd_percent, 2))

        # Should not be close to the limit
        assert fd_percent < 80, f"Too many file descriptors in use: {fd_percent:.1f}%"
