"""
Labgrid Runner

This package polls KernelCI for test jobs, matches them against locally
available labgrid devices, claims jobs, runs tests, and uploads results.

Components:
- service: Main runner service
- poller: Job polling from KernelCI API
- executor: Test execution using labgrid and pytest
- results: Result collection and submission
"""

__version__ = "0.1.0"
