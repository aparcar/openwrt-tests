"""
Health Check System for OpenWrt KernelCI

Provides device health monitoring:
- Periodic health checks
- Failure tracking and device disabling
- GitHub issue creation/closure
- Notification system
"""

from .scheduler import HealthCheckScheduler
from .notifications import NotificationManager
from .device_registry import DeviceRegistry

__all__ = [
    "HealthCheckScheduler",
    "NotificationManager",
    "DeviceRegistry",
]
