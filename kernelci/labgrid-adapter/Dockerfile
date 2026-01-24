# =============================================================================
# Labgrid KernelCI Adapter
# =============================================================================
#
# This container runs in each lab and provides the bridge between
# the KernelCI API and the local labgrid infrastructure.
#
# Features:
# - Pull-mode job polling (no inbound connections required)
# - pytest test execution
# - Firmware flashing via labgrid
# - Result submission to KernelCI
#
# =============================================================================

FROM python:3.13-slim

# Labels
LABEL org.opencontainers.image.title="Labgrid KernelCI Adapter"
LABEL org.opencontainers.image.description="Bridge between KernelCI and labgrid test labs"

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    openssh-client \
    # QEMU for emulated targets
    qemu-system-arm \
    qemu-system-mips \
    qemu-system-x86 \
    # Serial port tools
    picocom \
    # Network tools
    iputils-ping \
    netcat-openbsd \
    # Build tools (for some labgrid drivers)
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for layer caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY labgrid_kci_adapter/ ./labgrid_kci_adapter/

# Copy test suite from main repo (mounted or copied)
# These are typically mounted from the host
COPY tests/ ./tests/ 2>/dev/null || true
COPY conftest.py . 2>/dev/null || true
COPY targets/ ./targets/ 2>/dev/null || true

# Create non-root user
RUN useradd -m -s /bin/bash adapter
RUN chown -R adapter:adapter /app

# Create directories for caching and logs
RUN mkdir -p /app/cache /app/logs && chown adapter:adapter /app/cache /app/logs

# Switch to non-root user
USER adapter

# Environment
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Default command
CMD ["python", "-m", "labgrid_kci_adapter.service"]
