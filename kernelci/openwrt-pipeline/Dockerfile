# =============================================================================
# OpenWrt KernelCI Pipeline Services
# =============================================================================
#
# This Dockerfile builds the container image for all pipeline services:
# - firmware_trigger: Watches for new firmware
# - test_scheduler: Assigns jobs to labs
# - health_scheduler: Monitors device health
# - results_collector: Aggregates test results
#
# =============================================================================

FROM python:3.13-slim

# Labels
LABEL org.opencontainers.image.title="OpenWrt KernelCI Pipeline"
LABEL org.opencontainers.image.description="Pipeline services for OpenWrt firmware testing"
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
COPY openwrt_pipeline/ ./openwrt_pipeline/

# Create non-root user
RUN useradd -m -s /bin/bash pipeline
RUN chown -R pipeline:pipeline /app

# Switch to non-root user
USER pipeline

# Environment
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Default command (overridden by docker-compose)
CMD ["python", "-m", "openwrt_pipeline"]
