# =============================================================================
# OpenWrt Scheduler
# =============================================================================
#
# This Dockerfile builds the container image for the scheduler services:
# - firmware_trigger: Watches for new firmware
# - test_scheduler: Assigns jobs to labs
# - kcidb_bridge: Uploads results to KCIDB dashboard
#
# =============================================================================

FROM python:3.13-slim

# Labels
LABEL org.opencontainers.image.title="OpenWrt Scheduler"
LABEL org.opencontainers.image.description="Firmware discovery and test scheduling for OpenWrt"
LABEL org.opencontainers.image.source="https://github.com/openwrt/openwrt-tests"

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for layer caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY openwrt_scheduler/ ./openwrt_scheduler/

# Create non-root user
RUN useradd -m -s /bin/bash scheduler
RUN chown -R scheduler:scheduler /app

# Switch to non-root user
USER scheduler

# Environment
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Default command (overridden by docker-compose)
CMD ["python", "-m", "openwrt_scheduler"]
