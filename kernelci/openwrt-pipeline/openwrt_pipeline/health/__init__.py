"""
Health Check System for OpenWrt KernelCI

Provides device health monitoring:
- Periodic health checks
- Failure tracking and device disabling
- GitHub issue creation/closure
- Notification system
"""

from .device_registry import DeviceRegistry
from .notifications import NotificationManager
from .scheduler import HealthCheckScheduler

__all__ = [
    "HealthCheckScheduler",
    "NotificationManager",
    "DeviceRegistry",
]
