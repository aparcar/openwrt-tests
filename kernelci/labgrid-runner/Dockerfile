# =============================================================================
# Labgrid Runner
# =============================================================================
#
# Polls KernelCI for test jobs, runs tests on labgrid devices (including
# QEMU), and uploads results. Tests, targets, and strategies are
# volume-mounted from the host repository.
#
# =============================================================================

FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    openssh-client \
    qemu-system-x86 \
    qemu-system-arm \
    qemu-system-mips \
    picocom \
    iputils-ping \
    nmap \
    netcat-openbsd \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy adapter code
COPY labgrid_runner/ ./labgrid_runner/

# Create directories for caching and logs
RUN mkdir -p /app/cache /app/logs /app/tests /app/targets /app/strategies

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "labgrid_runner.service"]
