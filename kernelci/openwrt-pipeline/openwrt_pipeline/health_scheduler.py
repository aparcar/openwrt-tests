"""
Health Scheduler Service Entry Point

This module provides the entry point for running the health check
scheduler as a standalone service.

Usage:
    python -m openwrt_pipeline.health_scheduler

Or via Docker:
    docker compose run pipeline-health
"""

from .health.scheduler import main, run

if __name__ == "__main__":
    run()
