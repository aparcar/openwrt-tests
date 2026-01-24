# OpenWrt KernelCI Dashboard Components

This directory contains React components and specifications for
OpenWrt-specific dashboard views.

## Overview

The OpenWrt dashboard extends the KernelCI dashboard with:

1. **Device Fleet Status** - Visual overview of all test devices
2. **Firmware Matrix** - Test results across versions and devices
3. **Health Check Dashboard** - Device health monitoring
4. **PR Status View** - Test results for GitHub PRs
5. **Regression Tracking** - Identify test regressions

## Integration

These components are designed to work with the KernelCI dashboard
(https://github.com/kernelci/dashboard). They can be:

1. **Merged upstream** - Contribute OpenWrt-specific views to KernelCI
2. **Custom deployment** - Deploy a modified dashboard image
3. **Embedded** - Load components in an iframe

## Components

### DeviceFleetStatus

Shows the status of all devices across all labs with:
- Health status indicators (healthy/failing/disabled)
- Device features
- Lab grouping
- Quick actions (trigger health check, view logs)

### FirmwareMatrix

A matrix view showing test results:
- Rows: Devices
- Columns: Firmware versions (snapshot, stable, oldstable)
- Cells: Pass/fail/skip counts with links to details

### HealthCheckDashboard

Dedicated view for device health monitoring:
- Summary statistics
- Device list with status
- Recent health check results
- Issue links for failing devices

### PRStatusView

Shows test status for GitHub PRs:
- PR list with test status
- Test results per PR
- Links to GitHub

## API Requirements

These components expect the following API endpoints:

### Device Endpoints
```
GET  /api/v1/devices                    - List all devices with status
GET  /api/v1/devices/{id}               - Device details
POST /api/v1/devices/{id}/health-check  - Trigger health check
POST /api/v1/devices/{id}/enable        - Re-enable disabled device
POST /api/v1/devices/{id}/disable       - Manually disable device
```

### Firmware Endpoints
```
GET /api/v1/firmware                - List firmware with filters
GET /api/v1/firmware/{id}           - Firmware details
```

### Test Results Endpoints
```
GET /api/v1/results                 - Test results with filters
GET /api/v1/results/matrix          - Matrix data for firmware×device
```

### Health Check Endpoints
```
GET /api/v1/health/summary          - Overall health summary
GET /api/v1/health/history          - Health check history (with limit param)
```

### PR Testing Endpoints
```
GET /api/v1/pr/summaries            - List PR test summaries
GET /api/v1/pr/{number}/jobs        - Get jobs for specific PR
```

### Lab Endpoints
```
GET /api/v1/labs                    - List all labs
GET /api/v1/labs/{id}               - Lab details
```

### Statistics Endpoints
```
GET /api/v1/stats/daily             - Daily test statistics
GET /api/v1/stats/summary           - Overall summary
```

## Development

```bash
# Install dependencies
npm install

# Run development server
npm run dev

# Build for production
npm run build
```

## Deployment

The dashboard can be deployed via Docker Compose alongside the
other KernelCI services. See the main `docker-compose.yml` for
the dashboard service configuration.
