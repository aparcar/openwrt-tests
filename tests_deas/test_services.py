"""System services and daemons tests for OpenWRT."""

import re
import time

import pytest
from conftest import ubus_call


class TestServices:
    """Tests for system services and daemons."""

    def test_init_system(self, ssh_command):
        """Test init system is functioning properly."""
        # Check if procd is running (OpenWRT's init system)
        procd_check = ssh_command.run("pgrep -x procd")
        assert procd_check[2] == 0, "procd (init system) is not running"

        # Check procd is PID 1
        procd_pid = ssh_command.run_check("pgrep -x procd")[0].strip()
        assert procd_pid == "1", f"procd should be PID 1, but is PID {procd_pid}"

    def test_essential_services(self, ssh_command, results_bag):
        """Test essential system services are running."""
        essential_services = {
            "dropbear": "SSH daemon",
            "uhttpd": "Web server",
            "dnsmasq": "DNS/DHCP server",
            "netifd": "Network interface daemon",
            "logd": "Logging daemon",
        }

        running_services = {}
        missing_services = []

        for service, description in essential_services.items():
            # Check if service is running
            pid_check = ssh_command.run(f"pgrep -x {service}")

            if pid_check[2] == 0:
                running_services[service] = {
                    "status": "running",
                    "pid": pid_check[0].strip().split("\n")[0],
                    "description": description,
                }
            else:
                # Check if service is installed but not running
                init_script = ssh_command.run(f"test -x /etc/init.d/{service}")
                if init_script[2] == 0:
                    running_services[service] = {
                        "status": "stopped",
                        "description": description,
                    }
                    missing_services.append(service)
                else:
                    running_services[service] = {
                        "status": "not_installed",
                        "description": description,
                    }

        results_bag["running_services"] = running_services

        # Only fail for critical services that should always run
        critical = ["procd", "netifd"]
        for service in critical:
            if service in missing_services:
                assert False, f"Critical service {service} is not running"

    def test_service_init_scripts(self, ssh_command):
        """Test init scripts are properly configured."""
        # List all init scripts
        init_scripts = ssh_command.run_check("ls /etc/init.d/")

        for script in init_scripts[0].split():
            if script in [".", "..", "README"]:
                continue

            # Check if script is executable
            is_executable = ssh_command.run(f"test -x /etc/init.d/{script}")[2] == 0
            assert is_executable, f"Init script /etc/init.d/{script} is not executable"

            # Check basic script structure
            has_start = (
                ssh_command.run(f"grep -q 'start()' /etc/init.d/{script}")[2] == 0
            )
            has_boot = ssh_command.run(f"grep -q 'START=' /etc/init.d/{script}")[2] == 0

            # Should have at least one of these
            assert has_start or has_boot, (
                f"Init script {script} missing start function or START priority"
            )

    def test_enabled_services(self, ssh_command, results_bag):
        """Test which services are enabled at boot."""
        # Get all enabled services
        enabled_services = {}

        rc_scripts = ssh_command.run("ls /etc/rc.d/S* 2>/dev/null")
        if rc_scripts[2] == 0 and rc_scripts[0]:
            for link in rc_scripts[0].split():
                # Extract service name and priority
                match = re.match(r"/etc/rc.d/S(\d+)(.+)", link)
                if match:
                    priority = match.group(1)
                    service = match.group(2)
                    enabled_services[service] = int(priority)

        results_bag["enabled_services"] = enabled_services

        # Check that critical services are enabled
        assert "network" in enabled_services, "Network service is not enabled at boot"
        assert "boot" in enabled_services, "Boot service is not enabled"

    def test_service_management(self, ssh_command):
        """Test service start/stop/restart functionality."""
        # Use log service as it's safe to restart
        test_service = "log"

        # Check if service exists
        if ssh_command.run(f"test -x /etc/init.d/{test_service}")[2] != 0:
            pytest.skip(f"Service {test_service} not available for testing")

        # Get initial status
        ssh_command.run(f"/etc/init.d/{test_service} status")[2]

        # Test restart
        restart_result = ssh_command.run(f"/etc/init.d/{test_service} restart")
        assert restart_result[2] == 0, f"Failed to restart {test_service} service"

        # Give service time to restart
        time.sleep(1)

        # Verify service is running after restart
        final_status = ssh_command.run(f"/etc/init.d/{test_service} status")[2]
        assert final_status == 0, f"Service {test_service} not running after restart"

    def test_cron_service(self, ssh_command):
        """Test cron daemon functionality."""
        # Check if crond is running
        cron_pid = ssh_command.run("pgrep -x crond")

        if cron_pid[2] != 0:
            # Try busybox crond
            cron_pid = ssh_command.run("pgrep cron")
            if cron_pid[2] != 0:
                pytest.skip("Cron service not running")

        # Check crontab directory exists
        crontab_dir = ssh_command.run("test -d /etc/crontabs")[2] == 0
        assert crontab_dir, "/etc/crontabs directory missing"

        # Check if root crontab exists
        ssh_command.run("test -f /etc/crontabs/root")[2] == 0

        # Create a test cron job
        test_file = "/tmp/cron_test_marker"
        try:
            # Remove any existing test file
            ssh_command.run(f"rm -f {test_file}")

            # Add test cron job
            ssh_command.run_check(
                f"echo '* * * * * touch {test_file}' >> /etc/crontabs/root"
            )

            # Restart cron to pick up changes
            ssh_command.run("/etc/init.d/cron restart")

            # Wait for cron to run (up to 65 seconds)
            time.sleep(65)

            # Check if test file was created
            file_exists = ssh_command.run(f"test -f {test_file}")[2] == 0
            assert file_exists, "Cron job did not execute"

        finally:
            # Cleanup
            ssh_command.run(f"rm -f {test_file}")
            ssh_command.run("sed -i '/cron_test_marker/d' /etc/crontabs/root")
            ssh_command.run("/etc/init.d/cron restart")

    def test_syslog_service(self, ssh_command):
        """Test system logging service."""
        # Check if logd is running
        logd_pid = ssh_command.run("pgrep -x logd")
        assert logd_pid[2] == 0, "System logging daemon (logd) not running"

        # Test logging functionality
        test_message = f"OpenWRT test message {int(time.time())}"

        # Send test message
        ssh_command.run_check(f"logger -t test '{test_message}'")

        # Give log system time to process
        time.sleep(1)

        # Check if message appears in log
        log_check = ssh_command.run(f"logread | grep '{test_message}'")
        assert log_check[2] == 0, "Test message not found in system log"

        # Check log size limits
        log_size = ssh_command.run("uci get system.@system[0].log_size 2>/dev/null")
        if log_size[2] == 0:
            size_kb = int(log_size[0].strip())
            assert size_kb >= 64, f"Log size too small: {size_kb}KB"

    def test_dnsmasq_service(self, ssh_command):
        """Test DNS/DHCP service."""
        # Check if dnsmasq is running
        dnsmasq_pid = ssh_command.run("pgrep -x dnsmasq")

        if dnsmasq_pid[2] != 0:
            pytest.skip("dnsmasq not running")

        # Check if listening on DNS port
        dns_listen = ssh_command.run("netstat -tlunp | grep ':53'")
        assert dns_listen[2] == 0, "dnsmasq not listening on DNS port 53"

        # Test DNS resolution
        dns_test = ssh_command.run("nslookup localhost 127.0.0.1")
        assert dns_test[2] == 0, "Local DNS resolution failed"

        # Check DHCP configuration
        dhcp_config = ssh_command.run("uci show dhcp.lan 2>/dev/null")
        if dhcp_config[2] == 0:
            # Verify DHCP range is configured
            dhcp_start = ssh_command.run("uci get dhcp.lan.start 2>/dev/null")
            dhcp_limit = ssh_command.run("uci get dhcp.lan.limit 2>/dev/null")

            if dhcp_start[2] == 0 and dhcp_limit[2] == 0:
                start = int(dhcp_start[0].strip())
                limit = int(dhcp_limit[0].strip())
                assert start > 0, "Invalid DHCP start address"
                assert limit > 0, "Invalid DHCP limit"

    def test_uhttpd_service(self, ssh_command):
        """Test web server service."""
        # Check if uhttpd is running
        uhttpd_pid = ssh_command.run("pgrep -x uhttpd")

        if uhttpd_pid[2] != 0:
            pytest.skip("uhttpd web server not running")

        # Check listening ports
        http_ports = ssh_command.run("netstat -tlnp | grep uhttpd")
        assert http_ports[2] == 0, "uhttpd not listening on any ports"

        # Check for standard HTTP/HTTPS ports
        ports_output = http_ports[0]
        has_http = ":80" in ports_output or ":8080" in ports_output
        has_https = ":443" in ports_output or ":8443" in ports_output

        assert has_http or has_https, (
            "uhttpd not listening on standard HTTP/HTTPS ports"
        )

        # Test basic HTTP request
        if has_http:
            port = "80" if ":80" in ports_output else "8080"
            http_test = ssh_command.run(
                f"wget -q -O - http://localhost:{port}/ | head -20"
            )
            # Just check it returns something, don't verify content
            assert http_test[0], "Web server returned empty response"

    def test_ntpd_service(self, ssh_command):
        """Test NTP time synchronization service."""
        # Check if NTP is enabled
        ntp_enabled = ssh_command.run("uci get system.ntp.enabled 2>/dev/null")

        if ntp_enabled[2] != 0 or ntp_enabled[0].strip() != "1":
            pytest.skip("NTP not enabled")

        # Check if ntpd is running
        ntpd_pid = ssh_command.run("pgrep ntpd")

        if ntpd_pid[2] == 0:
            # Full ntpd is running
            # Check NTP peers
            ntp_peers = ssh_command.run("ntpd -p 2>/dev/null")
            if ntp_peers[2] == 0:
                assert "stratum" in ntp_peers[0].lower(), "No NTP peers found"
        else:
            # Check for busybox ntpd
            busybox_ntpd = ssh_command.run("ps | grep -v grep | grep ntpd")
            assert busybox_ntpd[2] == 0, "No NTP daemon running"

    def test_service_dependencies(self, ssh_command):
        """Test service dependency ordering."""
        # Get boot sequence
        boot_sequence = {}

        rc_scripts = ssh_command.run("ls -la /etc/rc.d/S* 2>/dev/null")
        if rc_scripts[2] == 0:
            for line in rc_scripts[0].split("\n"):
                match = re.search(r"S(\d+)(\S+)\s+->\s+\.\./init\.d/(\S+)", line)
                if match:
                    priority = int(match.group(1))
                    service = match.group(3)
                    boot_sequence[service] = priority

        # Verify critical service ordering
        if "boot" in boot_sequence and "network" in boot_sequence:
            assert boot_sequence["boot"] < boot_sequence["network"], (
                "Boot should start before network"
            )

        if "network" in boot_sequence and "firewall" in boot_sequence:
            assert boot_sequence["network"] < boot_sequence["firewall"], (
                "Network should start before firewall"
            )

    def test_watchdog_service(self, ssh_command):
        """Test hardware watchdog if available."""
        # Check if watchdog device exists
        watchdog_dev = ssh_command.run("test -c /dev/watchdog")[2] == 0

        if not watchdog_dev:
            pytest.skip("No hardware watchdog available")

        # Check if watchdog is being serviced
        ssh_command.run("pgrep -f watchdog")

        # Check kernel watchdog parameters
        watchdog_timeout = ssh_command.run(
            "cat /sys/class/watchdog/watchdog0/timeout 2>/dev/null"
        )
        if watchdog_timeout[2] == 0:
            timeout = int(watchdog_timeout[0].strip())
            assert timeout > 0, "Watchdog timeout not set"
            assert timeout <= 300, f"Watchdog timeout too high: {timeout}s"

    @pytest.mark.slow
    def test_service_restart_all(self, ssh_command):
        """Test restarting all services doesn't break the system."""
        # Get list of safe services to restart
        safe_services = ["log", "cron", "uhttpd", "dnsmasq"]

        failed_services = []

        for service in safe_services:
            if ssh_command.run(f"test -x /etc/init.d/{service}")[2] == 0:
                # Restart service
                result = ssh_command.run(f"/etc/init.d/{service} restart")
                if result[2] != 0:
                    failed_services.append(service)

                # Give service time to start
                time.sleep(1)

                # Verify service is running
                status = ssh_command.run(f"/etc/init.d/{service} status")
                if status[2] != 0:
                    failed_services.append(f"{service}_status")

        assert not failed_services, f"Failed to restart services: {failed_services}"

    def test_ubus_service(self, ssh_command):
        """Test ubus system message bus."""
        # Check if ubusd is running
        ubusd_pid = ssh_command.run("pgrep -x ubusd")
        assert ubusd_pid[2] == 0, "ubusd not running"

        # Test ubus functionality
        ubus_list = ssh_command.run_check("ubus list")
        assert len(ubus_list) > 5, "Too few ubus objects registered"

        # Test calling a simple ubus method
        system_info = ubus_call(ssh_command, "system", "info", {})
        assert "uptime" in system_info, "ubus system info call failed"
        assert "memory" in system_info, "ubus system info missing memory data"
