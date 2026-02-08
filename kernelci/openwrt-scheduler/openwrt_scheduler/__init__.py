"""
OpenWrt Scheduler

This package checks OpenWrt firmware servers for latest builds, stores
firmware in the KernelCI database, and schedules test jobs:

- firmware_trigger: Watches for new firmware from various sources
- test_scheduler: Assigns test jobs to available labs
- kcidb_bridge: Uploads results to KCIDB dashboard
"""

__version__ = "0.1.0"
