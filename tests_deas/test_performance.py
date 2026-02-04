"""Performance and stress tests for OpenWRT."""

import re
import statistics
import time

import pytest


class TestPerformance:
    """Tests for system performance and stress testing."""

    def test_boot_time(self, ssh_command, results_bag):
        """Measure and validate boot time."""
        # Get boot time from kernel
        uptime_info = ssh_command.run_check("cat /proc/uptime")
        uptime_seconds = float(uptime_info[0].split()[0])

        # Get time when init started
        dmesg_output = ssh_command.run_check(
            "dmesg | grep -E 'Freeing (unused|init)' | head -1"
        )

        if dmesg_output[0]:
            # Extract timestamp from dmesg
            match = re.search(r"\[\s*(\d+\.\d+)\]", dmesg_output[0])
            if match:
                kernel_to_init = float(match.group(1))
                results_bag["boot_time"] = {
                    "kernel_to_init_seconds": kernel_to_init,
                    "total_uptime_seconds": uptime_seconds,
                    "boot_phase": "complete",
                }

                # Boot should complete reasonably fast
                assert kernel_to_init < 60, (
                    f"Kernel to init took too long: {kernel_to_init}s"
                )

    def test_memory_bandwidth(self, ssh_command):
        """Test memory bandwidth using dd."""
        # Create test in memory (tmpfs)
        iterations = 3

        read_speeds = []
        write_speeds = []

        for i in range(iterations):
            # Write test
            time.time()
            write_result = ssh_command.run_check(
                "dd if=/dev/zero of=/tmp/perftest bs=1M count=10 2>&1"
            )
            time.time()

            # Parse write speed
            for line in write_result:
                if "MB/s" in line or "MiB/s" in line:
                    speed_match = re.search(r"(\d+\.?\d*)\s*M[Bi]/s", line)
                    if speed_match:
                        write_speeds.append(float(speed_match.group(1)))

            # Read test
            time.time()
            read_result = ssh_command.run_check(
                "dd if=/tmp/perftest of=/dev/null bs=1M 2>&1"
            )
            time.time()

            # Parse read speed
            for line in read_result:
                if "MB/s" in line or "MiB/s" in line:
                    speed_match = re.search(r"(\d+\.?\d*)\s*M[Bi]/s", line)
                    if speed_match:
                        read_speeds.append(float(speed_match.group(1)))

            # Cleanup
            ssh_command.run("rm -f /tmp/perftest")

        # Calculate averages
        if write_speeds:
            avg_write = statistics.mean(write_speeds)
            assert avg_write > 10, f"Memory write speed too slow: {avg_write:.1f} MB/s"

        if read_speeds:
            avg_read = statistics.mean(read_speeds)
            assert avg_read > 10, f"Memory read speed too slow: {avg_read:.1f} MB/s"

    def test_cpu_performance(self, ssh_command, results_bag):
        """Test CPU performance with basic benchmarks."""
        # Simple CPU benchmark using bc

        # Pi calculation benchmark
        start_time = time.time()
        ssh_command.run_check("echo 'scale=100; 4*a(1)' | bc -l > /dev/null")
        calc_time = time.time() - start_time

        results_bag["cpu_benchmark"] = {
            "pi_calculation_seconds": calc_time,
            "test_type": "bc_pi_100_digits",
        }

        # Should complete in reasonable time (adjust based on target hardware)
        assert calc_time < 10, f"CPU calculation took too long: {calc_time:.2f}s"

        # Integer operations benchmark
        start_time = time.time()
        ssh_command.run_check(
            "awk 'BEGIN {for(i=0;i<100000;i++) j=i*i; print j}' > /dev/null"
        )
        int_time = time.time() - start_time

        assert int_time < 5, f"Integer operations took too long: {int_time:.2f}s"

    def test_network_throughput_loopback(self, ssh_command):
        """Test network throughput on loopback interface."""
        # Check if nc (netcat) is available
        nc_check = ssh_command.run("which nc")
        if nc_check[2] != 0:
            pytest.skip("netcat not available for network testing")

        # Use dd and nc for basic throughput test
        test_size = 10 * 1024 * 1024  # 10MB
        port = 12345

        # Start receiver in background
        ssh_command.run(f"nc -l -p {port} > /dev/null &")
        time.sleep(1)

        # Send data
        start_time = time.time()
        ssh_command.run_check(
            f"dd if=/dev/zero bs=1024 count=10240 2>/dev/null | nc localhost {port}"
        )
        transfer_time = time.time() - start_time

        # Calculate throughput
        throughput_mbps = (test_size * 8) / (transfer_time * 1000000)

        # Loopback should be fast
        assert throughput_mbps > 100, (
            f"Loopback throughput too low: {throughput_mbps:.1f} Mbps"
        )

        # Cleanup
        ssh_command.run("pkill -f 'nc -l'")

    def test_filesystem_performance(self, ssh_command, results_bag):
        """Test filesystem read/write performance."""
        # Test different filesystems if available
        test_paths = {"/tmp": "tmpfs", "/overlay": "overlay", "/": "root"}

        results = {}

        for path, fs_type in test_paths.items():
            # Check if path exists and is writable
            if ssh_command.run(f"test -w {path}")[2] != 0:
                continue

            test_file = f"{path}/perftest.dat"

            # Small file test (1MB)
            write_result = ssh_command.run(
                f"dd if=/dev/zero of={test_file} bs=1024 count=1024 conv=fsync 2>&1"
            )

            if write_result[2] == 0:
                # Parse results
                for line in write_result:
                    if "MB/s" in line or "MiB/s" in line:
                        speed_match = re.search(r"(\d+\.?\d*)\s*M[Bi]/s", line)
                        if speed_match:
                            results[fs_type] = {
                                "write_speed_mbs": float(speed_match.group(1)),
                                "path": path,
                            }

            # Cleanup
            ssh_command.run(f"rm -f {test_file}")

        results_bag["filesystem_performance"] = results

        # At least tmpfs should be fast
        if "tmpfs" in results:
            assert results["tmpfs"]["write_speed_mbs"] > 5, (
                f"tmpfs write speed too slow: {results['tmpfs']['write_speed_mbs']:.1f} MB/s"
            )

    def test_process_creation_performance(self, ssh_command):
        """Test process creation and context switching performance."""
        # Time how long it takes to create many processes
        process_count = 100

        start_time = time.time()
        ssh_command.run_check(
            f"for i in $(seq 1 {process_count}); do true & done; wait"
        )
        creation_time = time.time() - start_time

        # Calculate rate
        processes_per_second = process_count / creation_time

        # Should be able to create processes reasonably fast
        assert processes_per_second > 50, (
            f"Process creation too slow: {processes_per_second:.1f} processes/second"
        )

    def test_interrupt_handling(self, ssh_command):
        """Test interrupt handling performance."""
        # Get initial interrupt counts
        initial_interrupts = ssh_command.run_check("cat /proc/interrupts")

        # Generate some system activity
        ssh_command.run_check("dd if=/dev/zero of=/dev/null bs=1M count=10")
        time.sleep(1)

        # Get final interrupt counts
        final_interrupts = ssh_command.run_check("cat /proc/interrupts")

        # Basic check that interrupts are being handled
        assert initial_interrupts != final_interrupts, "No interrupt activity detected"

    @pytest.mark.slow
    def test_sustained_load(self, ssh_command):
        """Test system stability under sustained load."""
        # Run a sustained workload
        duration = 30  # seconds

        # Start CPU load
        ssh_command.run(
            f"timeout {duration} sh -c 'while true; do echo scale=100; 4*a(1) | bc -l > /dev/null; done' &"
        )

        # Start memory load
        ssh_command.run(
            f"timeout {duration} sh -c 'while true; do dd if=/dev/zero of=/tmp/load bs=1M count=5 2>/dev/null; done' &"
        )

        # Monitor system during load
        check_interval = 5
        checks = duration // check_interval

        for i in range(checks):
            time.sleep(check_interval)

            # Check system responsiveness
            start = time.time()
            ssh_command.run_check("echo responsive")
            response_time = time.time() - start

            assert response_time < 2, (
                f"System unresponsive under load: {response_time:.1f}s"
            )

            # Check memory isn't exhausted
            free_mem = int(ssh_command.run_check("free -m | grep Mem:")[0].split()[3])
            assert free_mem > 5, f"System running out of memory: {free_mem}MB free"

        # Wait for load to finish
        time.sleep(2)

        # Cleanup
        ssh_command.run("rm -f /tmp/load")

    def test_network_latency(self, ssh_command, results_bag):
        """Test network latency and jitter."""
        # Ping loopback
        ping_result = ssh_command.run_check("ping -c 10 -i 0.2 127.0.0.1")

        # Parse ping statistics
        latencies = []
        for line in ping_result:
            if "time=" in line:
                match = re.search(r"time=(\d+\.?\d*)", line)
                if match:
                    latencies.append(float(match.group(1)))
            elif "min/avg/max" in line:
                match = re.search(r"(\d+\.?\d*)/(\d+\.?\d*)/(\d+\.?\d*)", line)
                if match:
                    results_bag["loopback_latency"] = {
                        "min_ms": float(match.group(1)),
                        "avg_ms": float(match.group(2)),
                        "max_ms": float(match.group(3)),
                        "jitter_ms": float(match.group(3)) - float(match.group(1)),
                    }

        if latencies:
            avg_latency = statistics.mean(latencies)
            assert avg_latency < 1.0, f"Loopback latency too high: {avg_latency:.2f}ms"

    def test_concurrent_connections(self, ssh_command):
        """Test handling of concurrent network connections."""
        # Check current connection limits
        max_conn = ssh_command.run_check("sysctl net.netfilter.nf_conntrack_max")[0]
        max_value = int(max_conn.split("=")[1].strip())

        # Test creating multiple connections
        port_base = 20000
        num_connections = min(50, max_value // 10)  # Don't overwhelm the system

        # Start listeners
        for i in range(num_connections):
            ssh_command.run(f"nc -l -p {port_base + i} > /dev/null 2>&1 &")

        time.sleep(2)

        # Count established connections
        nc_count = ssh_command.run("pgrep -c 'nc -l'")[0].strip()

        # Most should have started successfully
        assert int(nc_count) > num_connections * 0.8, (
            f"Failed to create concurrent connections: {nc_count}/{num_connections}"
        )

        # Cleanup
        ssh_command.run("pkill -f 'nc -l'")

    def test_memory_fragmentation(self, ssh_command):
        """Test memory fragmentation handling."""
        # Get initial memory state
        int(ssh_command.run_check("free -m | grep Mem:")[0].split()[3])

        # Allocate and free memory multiple times
        iterations = 10
        for i in range(iterations):
            # Allocate
            ssh_command.run(
                f"dd if=/dev/zero of=/tmp/frag{i} bs=1M count=2 2>/dev/null"
            )

        # Free half
        for i in range(0, iterations, 2):
            ssh_command.run(f"rm -f /tmp/frag{i}")

        # Allocate again
        ssh_command.run("dd if=/dev/zero of=/tmp/frag_large bs=1M count=3 2>/dev/null")

        # Check if allocation succeeded
        large_exists = ssh_command.run("test -f /tmp/frag_large")[2] == 0
        assert large_exists, "Memory too fragmented to allocate continuous block"

        # Cleanup
        ssh_command.run("rm -f /tmp/frag*")

    def test_cache_performance(self, ssh_command):
        """Test filesystem cache performance."""
        test_file = "/tmp/cache_test"
        size_mb = 5

        # Create test file
        ssh_command.run_check(
            f"dd if=/dev/urandom of={test_file} bs=1M count={size_mb} 2>/dev/null"
        )

        # First read (cold cache)
        ssh_command.run("sync && echo 3 > /proc/sys/vm/drop_caches 2>/dev/null")

        cold_start = time.time()
        ssh_command.run_check(f"dd if={test_file} of=/dev/null bs=1M 2>/dev/null")
        cold_time = time.time() - cold_start

        # Second read (warm cache)
        warm_start = time.time()
        ssh_command.run_check(f"dd if={test_file} of=/dev/null bs=1M 2>/dev/null")
        warm_time = time.time() - warm_start

        # Warm cache should be significantly faster
        assert warm_time < cold_time * 0.5, (
            f"Cache not effective: cold={cold_time:.2f}s, warm={warm_time:.2f}s"
        )

        # Cleanup
        ssh_command.run(f"rm -f {test_file}")
