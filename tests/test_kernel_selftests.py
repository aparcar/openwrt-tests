import os
import pytest


# @pytest.mark.lg_feature("kernel-selftests")
# def test_kernel_selftests_setup(shell_command):
#     """Setup kernel selftests on the device"""
#     # Check if device has internet connectivity
#     shell_command.run_check("ping -c 1 8.8.8.8", timeout=30)

#     # Create selftests directory
#     shell_command.run_check("mkdir -p /root/selftests")

#     # Download kernel selftests if not already present
#     result = shell_command.run("ls /root/selftests/Makefile")
#     if result[2] != 0:  # File doesn't exist
#         print("Downloading kernel selftests...")
#         shell_command.run_check("cd /root && wget -q https://github.com/torvalds/linux/archive/refs/heads/master.tar.gz", timeout=300)
#         shell_command.run_check("cd /root && tar -xzf master.tar.gz", timeout=120)
#         shell_command.run_check("cd /root && cp -r linux-master/tools/testing/selftests/* selftests/", timeout=60)
#         shell_command.run_check("cd /root && rm -rf master.tar.gz linux-master")


@pytest.mark.lg_feature("kernel-selftests")
def test_kernel_selftests_run(shell_command, record_property):
    """Run kernel selftests with command from environment"""

    # Get the test command from environment variable
    test_command = os.environ.get(
        "SELFTESTS_COMMAND", 'echo "No test command specified"'
    )
    record_property("selftests_command", test_command)

    print(f"Running kernel selftests command: {test_command}")

    # Change to selftests directory and run the command
    full_command = f"cd /root/ && {test_command}"

    # Run with generous timeout (30 minutes)
    result = shell_command.run(full_command, timeout=3600)

    # Record the output
    output = "\n".join(result[1]) if isinstance(result[1], list) else str(result[1])
    record_property("selftests_output", output)
    record_property("selftests_exit_code", result[2])

    # Print output for workflow logs
    print("=== KERNEL SELFTESTS OUTPUT ===")
    print(output)
    print("=== END OUTPUT ===")

    # The test passes if the command ran (exit code recorded for analysis)
    # We don't fail the pytest test based on selftest results
    print(f"Kernel selftests completed with exit code: {result[2]}")


@pytest.mark.lg_feature("kernel-selftests")
def test_kernel_selftests_cleanup(shell_command):
    """Optional cleanup after kernel selftests"""

    # Check disk space after tests
    result = shell_command.run("df -h /root")
    if result[2] == 0:
        print("Disk space after tests:")
        print("\n".join(result[1]) if isinstance(result[1], list) else str(result[1]))

    # Optionally clean up large files to free space
    cleanup = os.environ.get("SELFTESTS_CLEANUP", "false").lower()
    if cleanup == "true":
        print("Cleaning up selftests directory...")
        shell_command.run("rm -rf /root/selftests")
