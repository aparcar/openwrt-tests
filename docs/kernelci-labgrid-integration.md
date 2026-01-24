# KernelCI + Labgrid Integration for OpenWrt Testing

## Executive Summary

This document analyzes the integration of KernelCI as a backend for the OpenWrt testing infrastructure, replacing or supplementing GitHub Actions while maintaining the existing labgrid-based test framework and supporting decentralized test labs.

## Table of Contents

1. [Current Infrastructure Analysis](#current-infrastructure-analysis)
2. [KernelCI Architecture Overview](#kernelci-architecture-overview)
3. [Integration Options](#integration-options)
4. [Recommended Approach](#recommended-approach)
5. [Implementation Plan](#implementation-plan)
6. [Technical Specifications](#technical-specifications)
7. [Benefits and Trade-offs](#benefits-and-trade-offs)

---

## Current Infrastructure Analysis

### Existing Architecture

The current OpenWrt testing infrastructure uses:

| Component | Technology | Purpose |
|-----------|------------|---------|
| Test Framework | pytest | Test execution and assertions |
| Device Control | labgrid (custom fork) | Hardware abstraction and control |
| CI/CD | GitHub Actions | Job orchestration and UI |
| Infrastructure | Ansible | Lab provisioning |
| Package Manager | uv | Python dependencies |

### Test Labs

**7 Distributed Labs** with proxy-based access:
- labgrid-aparcar, labgrid-bastian, labgrid-blocktrron
- labgrid-leinelab, labgrid-hsn, labgrid-wigyori, labgrid-hauke

**38+ Target Devices** including:
- Real hardware: TP-Link, Bananapi, Linksys, GL.iNet, Raspberry Pi, etc.
- QEMU targets: x86-64, MALTA-BE (MIPS), ARMSR-ARMV8

### Current Workflows

1. **healthcheck.yml** - Daily device health monitoring
2. **pull_requests.yml** - PR validation on QEMU + hardware
3. **daily.yml** - Multi-version testing (snapshot, stable, oldstable)
4. **formal.yml** - Code quality checks

### Strengths to Preserve

- Labgrid's flexible device control and board-specific deployment
- Decentralized lab architecture with proxy-based access
- Feature-based test filtering (`@pytest.mark.lg_feature`)
- Dynamic matrix generation from YAML configurations
- Device reservation/locking mechanisms

---

## KernelCI Architecture Overview

### Core Components

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         KernelCI Ecosystem                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────────────────┐   │
│  │   Maestro   │────▶│   Events    │────▶│   External Systems      │   │
│  │  (Pipeline) │     │  (Pub/Sub)  │     │   (Labs, CI Systems)    │   │
│  └─────────────┘     └─────────────┘     └─────────────────────────┘   │
│         │                                            │                  │
│         │                                            │                  │
│         ▼                                            ▼                  │
│  ┌─────────────┐                         ┌─────────────────────────┐   │
│  │   Storage   │                         │       KCIDB(-ng)        │   │
│  │ (Artifacts) │                         │   (Results Database)    │   │
│  └─────────────┘                         └─────────────────────────┘   │
│                                                      │                  │
│                                                      ▼                  │
│                                          ┌─────────────────────────┐   │
│                                          │       Dashboard         │   │
│                                          │    (Web Interface)      │   │
│                                          └─────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Key Components

#### 1. Maestro (API + Pipeline)
- **Database abstraction** via FastAPI + MongoDB
- **Pub/Sub interface** for event-driven workflows
- **User authentication** with JWT tokens
- **Data ownership model** - users own submitted data

#### 2. KCIDB (Kernel CI Database)
- **Unified results repository** across CI systems
- **BigQuery backend** (production) or PostgreSQL (self-hosted)
- **KCIDB-ng** - new REST API for submissions
- **Standardized schema** for builds, boots, and tests

#### 3. Events System
- **Automatic events** when nodes change
- **Custom events** for coordination
- **Subscribers** receive triggers for new builds/tests

#### 4. Dashboard
- **Public web interface** at kernelci.org
- **Results visualization** and regression tracking
- **kci-dev CLI** for developer interaction

### Pull-Mode Architecture (New in 2025)

KernelCI's new **pull-mode architecture** addresses security concerns:

```
Traditional Push Mode:
  KernelCI ──push jobs──▶ Lab API (requires public exposure)

New Pull Mode:
  Lab ──poll for jobs──▶ KernelCI API (lab stays behind firewall)
  Lab ──submit results──▶ KCIDB-ng
```

**Benefits:**
- Labs don't need public API exposure
- Works with existing security policies
- Labs maintain full control over their infrastructure

---

## Integration Options

### Option 1: Full LAVA Migration

Replace labgrid with LAVA for full KernelCI integration.

| Pros | Cons |
|------|------|
| Native KernelCI support | Significant rework required |
| Automatic bisection | LAVA less flexible for non-standard boards |
| Established community | Loss of labgrid's board-specific deployment |
| | Steeper learning curve |

**Verdict:** Not recommended - loses labgrid advantages

### Option 2: Labgrid-to-KernelCI Adapter (Recommended)

Build an adapter between existing labgrid infrastructure and KernelCI.

| Pros | Cons |
|------|------|
| Preserves labgrid investment | Requires adapter development |
| Board-specific flexibility | May need upstream collaboration |
| Proven at Pengutronix | |
| Works with pull-mode | |

**Verdict:** Recommended - best balance of effort and benefit

### Option 3: Results-Only Integration (KCIDB)

Keep GitHub Actions but submit results to KCIDB for visibility.

| Pros | Cons |
|------|------|
| Minimal changes | No centralized orchestration |
| Quick implementation | Still running own CI |
| Results in KernelCI dashboard | Limited automation benefits |

**Verdict:** Good first step, can evolve to Option 2

### Option 4: Hybrid Approach

Use KernelCI for kernel-focused tests, GitHub Actions for OpenWrt-specific tests.

| Pros | Cons |
|------|------|
| Best tool for each job | Complexity of two systems |
| Gradual migration path | Coordination overhead |

**Verdict:** Viable long-term strategy

---

## Recommended Approach

### Phase-Based Integration

We recommend a **phased hybrid approach** starting with results submission and evolving toward full orchestration:

```
Phase 1: KCIDB Results Integration
    └── Submit test results to KCIDB for visibility

Phase 2: Labgrid-KernelCI Adapter
    └── Pull-mode adapter for job orchestration

Phase 3: Distributed Lab Federation
    └── Enable community labs to contribute

Phase 4: Full Integration
    └── Dashboard, bisection, regression tracking
```

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    OpenWrt Testing + KernelCI Integration                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌────────────────────┐      ┌────────────────────┐                        │
│  │   OpenWrt Build    │      │     KernelCI       │                        │
│  │   Infrastructure   │      │     Maestro        │                        │
│  └─────────┬──────────┘      └─────────┬──────────┘                        │
│            │                           │                                    │
│            │ firmware                  │ events (pull-mode)                 │
│            ▼                           ▼                                    │
│  ┌─────────────────────────────────────────────────────────────┐           │
│  │              labgrid-kernelci-adapter                        │           │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │           │
│  │  │ Job Poller  │  │ Test Runner │  │ Results Submitter   │  │           │
│  │  │ (pull-mode) │  │  (pytest)   │  │     (KCIDB-ng)      │  │           │
│  │  └─────────────┘  └─────────────┘  └─────────────────────┘  │           │
│  └─────────────────────────────────────────────────────────────┘           │
│                               │                                             │
│                               │ labgrid protocol                            │
│                               ▼                                             │
│  ┌─────────────────────────────────────────────────────────────┐           │
│  │                   Labgrid Coordinator                        │           │
│  │                    (existing infra)                          │           │
│  └─────────────────────────────────────────────────────────────┘           │
│            │                           │                     │              │
│            ▼                           ▼                     ▼              │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐      │
│  │  Lab: aparcar    │    │  Lab: leinelab   │    │  Lab: community  │      │
│  │  (existing)      │    │  (existing)      │    │  (new contrib)   │      │
│  └──────────────────┘    └──────────────────┘    └──────────────────┘      │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Implementation Plan

### Phase 1: KCIDB Results Integration (Weeks 1-4)

**Goal:** Submit existing test results to KCIDB for visibility in KernelCI dashboard.

#### Tasks

1. **Register with KernelCI**
   - Contact kernelci@lists.linux.dev
   - Request KCIDB submission credentials
   - Obtain JSON credentials file and origin ID

2. **Implement Results Formatter**
   ```python
   # kcidb_formatter.py
   class KCIDBFormatter:
       """Convert pytest/labgrid results to KCIDB schema."""

       def format_build(self, firmware_info: dict) -> dict:
           """Format OpenWrt build information."""
           return {
               "id": f"openwrt:{build_id}",
               "origin": "openwrt",
               "git_repository_url": "https://github.com/openwrt/openwrt",
               "git_commit_hash": firmware_info["commit"],
               "architecture": firmware_info["target"],
               "config_name": firmware_info["profile"],
               "valid": True,
           }

       def format_test(self, test_result: dict, build_id: str) -> dict:
           """Format test result for KCIDB."""
           return {
               "id": f"openwrt:{test_id}",
               "build_id": build_id,
               "origin": "openwrt",
               "path": test_result["name"],
               "status": "PASS" if test_result["passed"] else "FAIL",
               "start_time": test_result["start"],
               "duration": test_result["duration"],
               "environment": {
                   "description": test_result["device"],
               }
           }
   ```

3. **Create Submission Script**
   ```python
   # submit_to_kcidb.py
   import kcidb

   def submit_results(results_json: str, credentials_file: str):
       """Submit formatted results to KCIDB."""
       client = kcidb.Client(credentials_file)
       client.submit(kcidb.io.schema.validate(results_json))
   ```

4. **Integrate with GitHub Actions**
   ```yaml
   # .github/workflows/daily.yml
   - name: Submit results to KCIDB
     if: always()
     run: |
       python scripts/submit_to_kcidb.py \
         --results results.json \
         --credentials ${{ secrets.KCIDB_CREDENTIALS }}
   ```

#### Deliverables
- [ ] KCIDB credentials and origin registration
- [ ] Results formatter module
- [ ] Submission script
- [ ] GitHub Actions integration
- [ ] OpenWrt results visible on KernelCI dashboard

---

### Phase 2: Labgrid-KernelCI Adapter (Weeks 5-10)

**Goal:** Enable pull-mode job orchestration from KernelCI to labgrid labs.

#### Tasks

1. **Create Job Poller Service**
   ```python
   # labgrid_kci_adapter/poller.py
   import asyncio
   import httpx
   from kernelci.api import API

   class KernelCIJobPoller:
       """Poll KernelCI for pending test jobs."""

       def __init__(self, api_token: str, lab_name: str):
           self.api = API(token=api_token)
           self.lab_name = lab_name

       async def poll_jobs(self):
           """Poll for jobs matching our capabilities."""
           while True:
               jobs = await self.api.get_pending_jobs(
                   runtime="labgrid",
                   lab=self.lab_name,
                   capabilities=self.get_capabilities()
               )
               for job in jobs:
                   await self.handle_job(job)
               await asyncio.sleep(30)

       def get_capabilities(self) -> list:
           """Return list of devices/capabilities this lab offers."""
           # Parse from labnet.yaml
           return ["openwrt-one", "bananapi-r4", "qemu-x86-64", ...]
   ```

2. **Implement Test Executor Bridge**
   ```python
   # labgrid_kci_adapter/executor.py
   from labgrid import Environment, Target

   class LabgridTestExecutor:
       """Execute KernelCI jobs using labgrid."""

       def execute_job(self, job: dict) -> dict:
           """Run test job on labgrid target."""
           target_name = job["device_type"]
           test_plan = job["test_plan"]
           firmware_url = job["artifacts"]["firmware"]

           # Acquire labgrid target
           env = Environment(config_file=f"targets/{target_name}.yaml")
           target = env.get_target("main")

           try:
               # Download and flash firmware
               self.provision_firmware(target, firmware_url)

               # Run tests
               results = self.run_tests(target, test_plan)

               return {
                   "status": "complete",
                   "results": results
               }
           finally:
               target.cleanup()
   ```

3. **Define Job Schema for OpenWrt**
   ```yaml
   # config/openwrt-jobs.yaml
   jobs:
     openwrt-base-tests:
       runtime: labgrid
       test_plan:
         - test_shell
         - test_ssh
         - test_firmware_version
         - test_ubus_system_board
       device_types:
         - openwrt-one
         - bananapi-r4
         - linksys-e8450

     openwrt-wifi-tests:
       runtime: labgrid
       test_plan:
         - test_wifi_wpa3
         - test_wifi_wpa2
         - test_wifi_scan
       device_types:
         - openwrt-one  # has wifi feature
       required_features:
         - wifi
   ```

4. **Create Adapter Service**
   ```python
   # labgrid_kci_adapter/service.py
   class LabgridKernelCIAdapter:
       """Main adapter service coordinating KernelCI and labgrid."""

       def __init__(self, config: dict):
           self.poller = KernelCIJobPoller(config["kci_token"], config["lab"])
           self.executor = LabgridTestExecutor(config["labgrid"])
           self.submitter = KCIDBSubmitter(config["kcidb_credentials"])

       async def run(self):
           """Main service loop."""
           async for job in self.poller.poll_jobs():
               try:
                   result = await self.executor.execute_job(job)
                   await self.submitter.submit(result)
               except Exception as e:
                   await self.submitter.submit_error(job, e)
   ```

#### Deliverables
- [ ] Job poller service
- [ ] Test executor bridge
- [ ] OpenWrt job definitions
- [ ] Adapter service with systemd unit
- [ ] Documentation for lab operators

---

### Phase 3: Distributed Lab Federation (Weeks 11-16)

**Goal:** Enable community members to contribute test capacity using pull-mode.

#### Tasks

1. **Create Lab Onboarding Documentation**
   ```markdown
   # Contributing a Test Lab to OpenWrt KernelCI Testing

   ## Prerequisites
   - labgrid coordinator and exporter setup
   - One or more OpenWrt-compatible devices
   - Internet access for pulling jobs

   ## Setup Steps
   1. Install the labgrid-kci-adapter
   2. Configure your lab in labnet.yaml
   3. Register with OpenWrt KernelCI
   4. Start the adapter service
   ```

2. **Implement Lab Registration API**
   ```python
   # api/lab_registration.py
   class LabRegistry:
       """Manage distributed lab registration."""

       def register_lab(self, lab_info: dict):
           """Register a new community lab."""
           # Validate lab capabilities
           # Generate lab credentials
           # Add to lab registry
           pass

       def get_available_labs(self) -> list:
           """Return list of active labs and their capabilities."""
           pass
   ```

3. **Create Lab Health Monitoring**
   ```python
   # monitoring/lab_health.py
   class LabHealthMonitor:
       """Monitor health of distributed labs."""

       def check_lab_health(self, lab_id: str) -> dict:
           """Check if lab is responsive and functional."""
           return {
               "lab_id": lab_id,
               "status": "healthy",
               "last_seen": datetime.now(),
               "available_devices": ["device1", "device2"],
               "jobs_completed_24h": 42
           }
   ```

4. **Implement Job Distribution Logic**
   ```python
   # scheduler/job_distributor.py
   class JobDistributor:
       """Distribute jobs across available labs."""

       def assign_job(self, job: dict) -> str:
           """Assign job to most suitable lab."""
           required_device = job["device_type"]

           # Find labs with this device available
           available_labs = self.find_labs_with_device(required_device)

           # Select based on:
           # - Current load
           # - Historical reliability
           # - Geographic proximity to artifact storage
           return self.select_best_lab(available_labs, job)
   ```

#### Deliverables
- [ ] Lab onboarding documentation
- [ ] Lab registration system
- [ ] Health monitoring dashboard
- [ ] Job distribution across labs
- [ ] Community lab contribution guide

---

### Phase 4: Full Integration (Weeks 17-24)

**Goal:** Complete integration with KernelCI ecosystem features.

#### Tasks

1. **Implement Regression Detection**
   ```python
   # analysis/regression.py
   class RegressionDetector:
       """Detect test regressions across firmware versions."""

       def detect_regressions(self, current: dict, previous: dict) -> list:
           """Compare results and identify regressions."""
           regressions = []
           for test in current["tests"]:
               prev_result = self.find_previous(test, previous)
               if prev_result and prev_result["status"] == "PASS":
                   if test["status"] == "FAIL":
                       regressions.append({
                           "test": test["name"],
                           "device": test["device"],
                           "current_commit": current["commit"],
                           "last_good_commit": previous["commit"]
                       })
           return regressions
   ```

2. **Enable Automatic Bisection** (for kernel-related tests)
   ```yaml
   # config/bisection.yaml
   bisection:
     enabled: true
     triggers:
       - test_kernel_errors
       - test_boot
     git_repo: https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git
     max_iterations: 10
   ```

3. **Create Unified Dashboard**
   - Extend or embed KernelCI dashboard
   - Add OpenWrt-specific views
   - Device fleet status
   - Per-version test matrix

4. **Implement kci-dev Integration for OpenWrt**
   ```bash
   # Example: Check OpenWrt test results
   kci-dev results --origin openwrt --tree openwrt/main

   # Compare firmware versions
   kci-dev results compare --origin openwrt \
     --commit abc123 --commit def456
   ```

#### Deliverables
- [ ] Regression detection system
- [ ] Bisection support for kernel tests
- [ ] Unified dashboard view
- [ ] kci-dev OpenWrt integration
- [ ] Full documentation

---

## Technical Specifications

### KCIDB Schema Mapping

| OpenWrt Concept | KCIDB Object | Notes |
|-----------------|--------------|-------|
| Firmware build | `build` | Target + profile + version |
| Device test run | `test` | One per device per test suite |
| Individual test | `test` child | Nested under device test |
| Console log | `log_url` | Link to artifact storage |

### API Endpoints

#### Maestro API (Pull Mode)
```
GET  /api/jobs?runtime=labgrid&status=pending
POST /api/jobs/{id}/start
POST /api/jobs/{id}/complete
```

#### KCIDB-ng API
```
POST /submit           # Submit results
GET  /status?id={id}   # Check submission status
```

### Environment Variables

```bash
# KernelCI Integration
KCI_API_URL=https://api.kernelci.org
KCI_API_TOKEN=<token>
KCIDB_CREDENTIALS=/path/to/credentials.json

# Lab Configuration
LG_COORDINATOR=coordinator.example.org
LG_LAB_NAME=openwrt-community-lab-1
```

### Docker Deployment

```dockerfile
# Dockerfile for labgrid-kci-adapter
FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY labgrid_kci_adapter/ ./labgrid_kci_adapter/
COPY config/ ./config/

CMD ["python", "-m", "labgrid_kci_adapter.service"]
```

```yaml
# docker-compose.yml
version: '3.8'
services:
  adapter:
    build: .
    environment:
      - KCI_API_TOKEN=${KCI_API_TOKEN}
      - KCIDB_CREDENTIALS=/secrets/kcidb.json
    volumes:
      - ./secrets:/secrets:ro
      - ./config:/app/config:ro
    restart: unless-stopped
```

---

## Benefits and Trade-offs

### Benefits

| Benefit | Description |
|---------|-------------|
| **Unified Dashboard** | Results visible alongside kernel.org testing |
| **Community Visibility** | OpenWrt testing visible to wider community |
| **Decentralized Labs** | Pull-mode enables secure lab contribution |
| **Regression Detection** | Automatic detection across versions |
| **Standards Compliance** | Aligns with Linux ecosystem practices |
| **Reduced Maintenance** | Leverage KernelCI infrastructure |
| **Bisection** | For kernel-related regressions |

### Trade-offs

| Trade-off | Mitigation |
|-----------|------------|
| Development effort | Phased approach, reuse existing code |
| New dependencies | KCIDB is well-maintained |
| Learning curve | Good documentation, active community |
| Not kernel-focused | KernelCI supports firmware testing |

### Comparison: Current vs. Proposed

| Aspect | Current (GitHub Actions) | Proposed (KernelCI) |
|--------|-------------------------|---------------------|
| Orchestration | GitHub-hosted | KernelCI Maestro |
| Results UI | GitHub Pages | KernelCI Dashboard |
| Lab security | Direct access needed | Pull-mode (firewall-safe) |
| Community labs | Manual setup | Federated registration |
| Regression tracking | Manual/issues | Automatic detection |
| Standards | Custom | Linux ecosystem standard |

---

## Next Steps

1. **Immediate:** Review this document and gather feedback
2. **Week 1:** Contact KernelCI community (kernelci@lists.linux.dev)
3. **Week 2:** Request KCIDB credentials and test submission
4. **Week 3-4:** Implement Phase 1 (results integration)
5. **Ongoing:** Iterate based on feedback and requirements

---

## References

- [KernelCI Architecture](https://docs.kernelci.org/intro/architecture/)
- [Maestro Documentation](https://docs.kernelci.org/maestro/)
- [KCIDB Submitter Guide](https://docs.kernelci.org/kcidb/submitter_guide/)
- [KCIDB GitHub](https://github.com/kernelci/kcidb)
- [Simple KernelCI Labs with Labgrid (LPC 2022)](https://lpc.events/event/16/contributions/1313/)
- [kci-dev Tool](https://kci.dev/)
- [Strengthening KernelCI: New Architecture (2025)](https://www.collabora.com/news-and-blog/blog/2025/11/17/strengthening-kernelci-new-architecture-storage-and-integrations/)
- [LAVA vs labgrid Discussion](https://github.com/labgrid-project/labgrid/discussions/1139)

---

*Document created: January 2025*
*Author: Claude Code Assistant*
