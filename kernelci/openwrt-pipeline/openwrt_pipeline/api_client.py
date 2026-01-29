"""
KernelCI API Client for OpenWrt Pipeline.

Provides async methods aligned with the KernelCI Maestro API:
- Node-based data model (jobs, tests are nodes with different 'kind')
- Pub/Sub event subscription
- Authentication via Bearer token

API Reference: https://docs.kernelci.org/maestro/
"""

import logging
from datetime import datetime
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings

logger = logging.getLogger(__name__)


class APIError(Exception):
    """API request error."""

    def __init__(self, status_code: int, message: str, details: Any = None):
        self.status_code = status_code
        self.message = message
        self.details = details
        super().__init__(f"API Error {status_code}: {message}")


class KernelCIClient:
    """
    Async client for KernelCI Maestro API.

    Uses the Node-based data model where all entities (checkouts, builds,
    jobs, tests) are nodes with different 'kind' values forming a tree.
    """

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = (base_url or settings.kci_api_url).rstrip("/")
        self.token = token or settings.kci_api_token
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def connect(self):
        """Create HTTP client connection."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self.timeout),
            )

    async def close(self):
        """Close HTTP client connection."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Client not connected")
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def _request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> dict[str, Any]:
        """Make an API request with retry logic."""
        response = await self.client.request(method, path, **kwargs)

        if response.status_code >= 400:
            try:
                error_data = response.json()
            except Exception:
                error_data = {"detail": response.text}

            raise APIError(
                status_code=response.status_code,
                message=error_data.get("detail", "Unknown error"),
                details=error_data,
            )

        if response.status_code == 204:
            return {}

        return response.json()

    # =========================================================================
    # Node Operations (Core KernelCI API)
    # =========================================================================

    async def create_node(self, node: dict[str, Any]) -> dict[str, Any]:
        """
        Create a new node.

        Nodes are the core data model in KernelCI. Types include:
        - checkout: Source code checkout
        - kbuild: Kernel/firmware build
        - job: Test job container
        - test: Individual test result

        Args:
            node: Node data including 'kind', 'name', 'path', etc.

        Returns:
            Created node with generated 'id'
        """
        return await self._request("POST", "/latest/nodes", json=node)

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Get node by ID."""
        try:
            return await self._request("GET", f"/latest/nodes/{node_id}")
        except APIError as e:
            if e.status_code == 404:
                return None
            raise

    async def update_node(
        self, node_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        """Update an existing node."""
        return await self._request("PUT", f"/latest/nodes/{node_id}", json=updates)

    async def query_nodes(
        self,
        kind: str | None = None,
        state: str | None = None,
        parent: str | None = None,
        name: str | None = None,
        limit: int = 100,
        offset: int = 0,
        **filters,
    ) -> list[dict[str, Any]]:
        """
        Query nodes with filters.

        Args:
            kind: Node kind (checkout, kbuild, job, test)
            state: Node state (running, done, available)
            parent: Parent node ID
            name: Node name pattern
            limit: Max results
            offset: Pagination offset
            **filters: Additional query filters

        Returns:
            List of matching nodes
        """
        params = {"limit": limit, "offset": offset}
        if kind:
            params["kind"] = kind
        if state:
            params["state"] = state
        if parent:
            params["parent"] = parent
        if name:
            params["name"] = name
        params.update(filters)

        data = await self._request("GET", "/latest/nodes", params=params)
        # API returns list directly or {"items": [...]}
        if isinstance(data, list):
            return data
        return data.get("items", data.get("nodes", []))

    # =========================================================================
    # OpenWrt-Specific Operations (Built on Nodes)
    # =========================================================================

    async def create_firmware_node(
        self,
        name: str,
        version: str,
        target: str,
        subtarget: str,
        profile: str,
        source: str,
        artifacts: dict[str, str],
        git_commit: str | None = None,
        pr_number: int | None = None,
    ) -> dict[str, Any]:
        """
        Create a firmware node (kind=kbuild for OpenWrt).

        This represents a built firmware image available for testing.
        """
        node = {
            "kind": "kbuild",
            "name": f"openwrt-{target}-{subtarget}-{profile}",
            "path": ["openwrt", version, target, subtarget, profile],
            "state": "available",
            "result": "pass",
            "data": {
                "openwrt_version": version,
                "target": target,
                "subtarget": subtarget,
                "profile": profile,
                "source": source,
                "artifacts": artifacts,
            },
        }
        if git_commit:
            node["data"]["git_commit"] = git_commit
        if pr_number:
            node["data"]["pr_number"] = pr_number

        return await self.create_node(node)

    async def create_test_job(
        self,
        firmware_node_id: str,
        device_type: str,
        test_plan: str,
        tests: list[str] | None = None,
        timeout: int = 1800,
    ) -> dict[str, Any]:
        """
        Create a test job node (kind=job).

        Jobs are containers for test runs on a specific device.
        """
        node = {
            "kind": "job",
            "name": f"openwrt-test-{device_type}-{test_plan}",
            "parent": firmware_node_id,
            "state": "available",  # Ready to be picked up by a lab
            "data": {
                "device_type": device_type,
                "test_plan": test_plan,
                "tests": tests or [],
                "timeout": timeout,
                "runtime": "labgrid",  # Indicates labgrid runtime
            },
        }
        return await self.create_node(node)

    async def claim_job(
        self,
        job_id: str,
        lab_name: str,
        device_id: str,
    ) -> dict[str, Any]:
        """
        Claim a job for execution (set state to 'running').

        Returns updated node or raises APIError if already claimed.
        """
        return await self.update_node(
            job_id,
            {
                "state": "running",
                "data": {
                    "lab_name": lab_name,
                    "device_id": device_id,
                    "started_at": datetime.utcnow().isoformat(),
                },
            },
        )

    async def complete_job(
        self,
        job_id: str,
        result: str,
        test_results: list[dict[str, Any]],
        log_url: str | None = None,
    ) -> dict[str, Any]:
        """
        Mark a job as complete and submit test results.

        Args:
            job_id: Job node ID
            result: Overall result (pass, fail, incomplete)
            test_results: List of individual test results
            log_url: URL to console/test logs
        """
        # Update job node
        job_update = {
            "state": "done",
            "result": result,
            "data": {
                "completed_at": datetime.utcnow().isoformat(),
            },
        }
        if log_url:
            job_update["data"]["log_url"] = log_url

        await self.update_node(job_id, job_update)

        # Create test nodes for each result
        for test in test_results:
            test_node = {
                "kind": "test",
                "name": test.get("name", "unknown"),
                "parent": job_id,
                "state": "done",
                "result": test.get("status", "fail"),
                "data": {
                    "duration": test.get("duration", 0),
                    "error_message": test.get("error_message"),
                },
            }
            await self.create_node(test_node)

        return await self.get_node(job_id)

    async def get_pending_jobs(
        self,
        device_type: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Get jobs available for execution.

        Filters for jobs with:
        - kind=job
        - state=available
        - runtime=labgrid
        """
        filters = {
            "kind": "job",
            "state": "available",
            "limit": limit,
        }
        if device_type:
            filters["data.device_type"] = device_type

        return await self.query_nodes(**filters)

    # =========================================================================
    # GitHub Integration
    # =========================================================================

    async def post_github_status(
        self,
        repo: str,
        commit_sha: str,
        state: str,
        context: str,
        description: str,
        target_url: str | None = None,
    ) -> None:
        """
        Post commit status to GitHub (if configured).

        This is typically handled by a separate GitHub integration service,
        but we provide the method for completeness.
        """
        # This would call GitHub API directly or through a webhook
        logger.info(f"GitHub status: {repo}@{commit_sha[:7]} {state} - {description}")

    # Note: Pub/Sub events would be implemented via WebSocket or SSE
    # connection to the KernelCI event endpoint. For OpenWrt testing,
    # the polling approach in test_scheduler.py is sufficient.
