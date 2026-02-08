"""
Firmware source watchers for OpenWrt KernelCI.

This module provides watchers for different firmware sources:
- official: Official OpenWrt releases from downloads.openwrt.org
- github_pr: Firmware artifacts from GitHub Pull Requests
- custom: Custom firmware uploads via API
- buildbot: Integration with OpenWrt Buildbot
"""

from .base import FirmwareSource
from .custom import CustomFirmwareUploader
from .github_pr import GitHubPRSource
from .official import OfficialReleaseSource

__all__ = [
    "FirmwareSource",
    "OfficialReleaseSource",
    "GitHubPRSource",
    "CustomFirmwareUploader",
]
