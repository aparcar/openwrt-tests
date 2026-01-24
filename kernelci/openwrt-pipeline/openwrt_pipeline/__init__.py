"""
OpenWrt KernelCI Pipeline Services

This package provides the pipeline services for OpenWrt firmware testing:

- firmware_trigger: Watches for new firmware from various sources
- test_scheduler: Assigns test jobs to available labs
- health_scheduler: Monitors device health
- results_collector: Aggregates and stores test results
"""

__version__ = "0.1.0"
