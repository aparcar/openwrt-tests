"""
KCIDB Bridge Service - Resolves full commit hashes from GitHub
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
import httpx
import jwt

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

KCI_API_URL = os.environ.get("KCI_API_URL", "http://kernelci-api:8000")
KCI_API_TOKEN = os.environ.get("KCI_API_TOKEN", "")
KCIDB_URL = os.environ.get("KCIDB_URL", "http://host.docker.internal:8080")
KCIDB_ORIGIN = os.environ.get("KCIDB_ORIGIN", "openwrt")
KCIDB_SECRET = os.environ.get("KCIDB_SECRET", "")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))

# Maximum size for log_excerpt (KCIDB limit is 16384)
MAX_LOG_EXCERPT_SIZE = 16000

# Cache for resolved commit hashes
_commit_cache: dict[str, str] = {}

# Cache for fetched log excerpts
_log_cache: dict[str, str] = {}


async def fetch_log_excerpt(log_url: str) -> str | None:
    """
    Fetch log content from URL and extract a relevant excerpt.
    
    Returns up to MAX_LOG_EXCERPT_SIZE characters, prioritizing:
    1. Test results section (PASSED/FAILED summary)
    2. Error messages and failures
    3. Last portion of the log if nothing specific found
    """
    if not log_url:
        return None
    
    if log_url in _log_cache:
        return _log_cache[log_url]
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(log_url)
            if resp.status_code != 200:
                logger.warning(f"Failed to fetch log: {resp.status_code}")
                return None
            
            content = resp.text
            excerpt = extract_log_excerpt(content)
            _log_cache[log_url] = excerpt
            return excerpt
    except Exception as e:
        logger.warning(f"Failed to fetch log from {log_url}: {e}")
        return None


def extract_log_excerpt(content: str) -> str:
    """
    Extract the most relevant portion of a log file.
    
    Prioritizes pytest test results and error messages.
    """
    if not content:
        return ""
    
    lines = content.split('\n')
    
    # Look for pytest summary section (most relevant for test results)
    summary_start = -1
    for i, line in enumerate(lines):
        # pytest summary markers
        if '====' in line and ('passed' in line.lower() or 'failed' in line.lower() or 'error' in line.lower()):
            summary_start = max(0, i - 50)  # Include 50 lines before summary
            break
        if line.startswith('FAILED ') or line.startswith('ERROR '):
            summary_start = max(0, i - 20)
            break
    
    if summary_start >= 0:
        # Get from summary to end
        excerpt_lines = lines[summary_start:]
        excerpt = '\n'.join(excerpt_lines)
    else:
        # No summary found - take the last portion of the log
        excerpt = content
    
    # Truncate to max size
    if len(excerpt) > MAX_LOG_EXCERPT_SIZE:
        excerpt = excerpt[-MAX_LOG_EXCERPT_SIZE:]
        # Find first newline to avoid cutting mid-line
        first_newline = excerpt.find('\n')
        if first_newline > 0:
            excerpt = excerpt[first_newline + 1:]
    
    return excerpt


def generate_kcidb_token():
    now = datetime.now(timezone.utc)
    payload = {
        "origin": KCIDB_ORIGIN,
        "gendate": now.isoformat(),
        "exp": int(now.timestamp()) + 3600,
    }
    return jwt.encode(payload, KCIDB_SECRET, algorithm="HS256")


async def resolve_full_commit(short_hash: str) -> str:
    """Resolve short commit hash to full 40-char hash via GitHub API."""
    if not short_hash:
        return "0" * 40

    short_hash = ''.join(c for c in short_hash.lower() if c in '0123456789abcdef')

    if len(short_hash) >= 40:
        return short_hash[:40]

    if short_hash in _commit_cache:
        return _commit_cache[short_hash]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://api.github.com/repos/openwrt/openwrt/commits/{short_hash}",
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                full_hash = data.get("sha", "")
                if full_hash and len(full_hash) == 40:
                    _commit_cache[short_hash] = full_hash
                    logger.info(f"Resolved commit {short_hash} -> {full_hash}")
                    return full_hash
            else:
                logger.warning(f"GitHub API returned {resp.status_code} for {short_hash}")
    except Exception as e:
        logger.warning(f"Failed to resolve commit {short_hash}: {e}")

    padded = short_hash + "0" * (40 - len(short_hash))
    _commit_cache[short_hash] = padded
    return padded


def fix_timestamp(ts: str) -> str:
    if not ts:
        return datetime.now(timezone.utc).isoformat()
    if "+" not in ts and "Z" not in ts:
        return ts + "+00:00"
    return ts


def node_to_kcidb_checkout(node: dict, full_commit: str, checkout_id: str) -> dict:
    data = node.get("data", {})
    kernel_rev = data.get("kernel_revision", {})

    return {
        "id": checkout_id,
        "origin": KCIDB_ORIGIN,
        "tree_name": kernel_rev.get("tree", "openwrt"),
        "git_repository_url": "https://github.com/openwrt/openwrt.git",
        "git_repository_branch": kernel_rev.get("branch", "main"),
        "git_commit_hash": full_commit,
        "patchset_hash": "",
        "start_time": fix_timestamp(node.get("created", "")),
        "valid": True,
    }


def node_to_kcidb_build(node: dict, checkout_id: str) -> dict:
    data = node.get("data", {})
    target = data.get("target", "")
    subtarget = data.get("subtarget", "")
    profile = data.get("profile", "")

    # architecture = target/subtarget (e.g., ath79/generic)
    # config_name = profile only (e.g., tplink_tl-wdr3600-v1)
    architecture = f"{target}_{subtarget}" if target and subtarget else target
    config_name = profile

    # Builds use 'valid' field (boolean), not 'status'
    # A build is valid if the firmware was successfully created
    result = node.get("result")
    is_valid = result == "pass"

    return {
        "id": f"{KCIDB_ORIGIN}:{node['id']}",
        "origin": KCIDB_ORIGIN,
        "checkout_id": checkout_id,
        "comment": f"OpenWrt {data.get('openwrt_version', '')} - {target}/{subtarget}/{profile}",
        "start_time": fix_timestamp(node.get("created", "")),
        "valid": is_valid,
        "architecture": architecture,
        "config_name": config_name,
        "input_files": [],  # Required by KCIDB schema
    }


class KCIDBBridge:
    def __init__(self):
        self.kci_client = None
        self.kcidb_client = None
        self.processed_ids = set()

    async def start(self):
        self.kci_client = httpx.AsyncClient(
            base_url=KCI_API_URL,
            headers={"Authorization": f"Bearer {KCI_API_TOKEN}"},
            timeout=30.0,
        )
        self.kcidb_client = httpx.AsyncClient(
            base_url=KCIDB_URL,
            timeout=30.0,
        )
        logger.info(f"KCIDB Bridge started - KCI: {KCI_API_URL}, KCIDB: {KCIDB_URL}")

    async def stop(self):
        if self.kci_client:
            await self.kci_client.aclose()
        if self.kcidb_client:
            await self.kcidb_client.aclose()

    async def get_unprocessed_nodes(self, kind: str, limit: int = 100, state: str = None) -> list:
        try:
            params = {"kind": kind, "limit": limit}
            if state:
                params["state"] = state
            resp = await self.kci_client.get(
                "/latest/nodes",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            return [n for n in items if n.get("id") not in self.processed_ids]
        except Exception as e:
            logger.error(f"Error fetching nodes: {e}")
            return []

    async def submit_to_kcidb(self, data: dict) -> bool:
        try:
            token = generate_kcidb_token()
            resp = await self.kcidb_client.post(
                "/submit",
                json=data,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                return True
            else:
                logger.warning(f"KCIDB submission failed: {resp.status_code} {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Error submitting to KCIDB: {e}")
            return False

    async def process_kbuilds(self):
        nodes = await self.get_unprocessed_nodes("kbuild", limit=50)
        if not nodes:
            return 0

        checkouts = []
        builds = []

        # Group by branch so all builds from the same branch share one checkout.
        # Snapshots (main) may have different commits per architecture, but the
        # dashboard groups by checkout — one checkout per branch keeps them together.
        branch_map: dict[str, list[dict]] = {}
        for node in nodes:
            data = node.get("data", {})
            kernel_rev = data.get("kernel_revision", {})
            branch = kernel_rev.get("branch", "main")
            branch_map.setdefault(branch, []).append(node)

        for branch, branch_nodes in branch_map.items():
            # Use the first node's commit as the checkout commit
            # (for releases all commits are the same; for snapshots pick one)
            first_data = branch_nodes[0].get("data", {})
            first_rev = first_data.get("kernel_revision", {})
            short_commit = first_rev.get("commit", "")
            full_commit = await resolve_full_commit(short_commit)

            # Stable checkout ID per branch so re-submissions update the same record
            checkout_id = f"{KCIDB_ORIGIN}:checkout:{branch}"

            # Create one checkout for this branch
            checkouts.append(node_to_kcidb_checkout(branch_nodes[0], full_commit, checkout_id))

            # All builds in this branch point to the shared checkout
            for node in branch_nodes:
                builds.append(node_to_kcidb_build(node, checkout_id))

        if checkouts:
            submission = {
                "version": {"major": 4, "minor": 3},
                "checkouts": checkouts,
                "builds": builds,
            }

            if await self.submit_to_kcidb(submission):
                logger.info(f"Submitted {len(builds)} builds to KCIDB")
                for node in nodes:
                    self.processed_ids.add(node["id"])
                return len(builds)

        return 0

    async def process_tests(self):
        # Process both "test" and "job" kinds - jobs contain test results
        nodes = []
        for kind in ["test", "job"]:
            # For jobs, filter by state=done to get completed jobs
            # Use larger limit to catch all recent jobs
            if kind == "job":
                kind_nodes = await self.get_unprocessed_nodes(kind, limit=500, state="done")
                # Only process jobs that have results
                kind_nodes = [n for n in kind_nodes if n.get("result")]
            else:
                kind_nodes = await self.get_unprocessed_nodes(kind, limit=50)
            nodes.extend(kind_nodes)

        if not nodes:
            return 0

        tests = []
        result_map = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP", "error": "ERROR", "incomplete": "MISS"}

        for node in nodes:
            data = node.get("data", {})
            result = node.get("result", "")
            status = result_map.get(result, "MISS")

            # For jobs, use parent as build_id (firmware node)
            parent_id = node.get("parent", "")

            # Build test path from job data (KCIDB uses dot-separated paths)
            device_type = data.get("device_type", "unknown")
            test_plan = data.get("test_plan", "unknown")
            lab_name = data.get("lab_name", "unknown")
            log_url = data.get("log_url")

            # Fetch log excerpt if log_url is available
            log_excerpt = None
            if log_url:
                log_excerpt = await fetch_log_excerpt(log_url)

            # Check if job has individual test results
            test_results = data.get("test_results", [])

            if test_results:
                # Submit individual test results for detailed view
                for test_result in test_results:
                    test_name = test_result.get("test_name", "unknown")
                    test_status = result_map.get(test_result.get("status", ""), "MISS")
                    # Path format: device.plan.test_name (e.g., bananapi_bpi-r4.boot.test_shell)
                    test_path = f"{device_type}.{test_plan}.{test_name}"

                    test_entry = {
                        "id": f"{KCIDB_ORIGIN}:{node['id']}:{test_name}",
                        "origin": KCIDB_ORIGIN,
                        "build_id": f"{KCIDB_ORIGIN}:{parent_id}" if parent_id else None,
                        "path": test_path,
                        "start_time": fix_timestamp(test_result.get("start_time") or data.get("started_at") or node.get("created", "")),
                        "status": test_status,
                        "waived": False,
                        "environment": {
                            "comment": f"Device: {device_type}, Lab: {lab_name}",
                            "misc": {
                                "platform": device_type,
                            },
                        },
                    }

                    # Add log URL and excerpt if available
                    if log_url:
                        test_entry["log_url"] = log_url
                    if log_excerpt:
                        test_entry["log_excerpt"] = log_excerpt

                    # Add error message if test failed
                    if test_result.get("error_message"):
                        test_entry["comment"] = test_result["error_message"][:500]

                    tests.append(test_entry)
            else:
                # No individual results - submit job-level test entry
                test_path = f"{device_type}.{test_plan}"

                test_entry = {
                    "id": f"{KCIDB_ORIGIN}:{node['id']}",
                    "origin": KCIDB_ORIGIN,
                    "build_id": f"{KCIDB_ORIGIN}:{parent_id}" if parent_id else None,
                    "path": test_path,
                    "start_time": fix_timestamp(data.get("started_at") or node.get("created", "")),
                    "status": status,
                    "waived": False,
                    "environment": {
                        "comment": f"Device: {device_type}, Lab: {lab_name}",
                        "misc": {
                            "platform": device_type,
                        },
                    },
                }

                if log_url:
                    test_entry["log_url"] = log_url
                if log_excerpt:
                    test_entry["log_excerpt"] = log_excerpt

                tests.append(test_entry)

        if tests:
            submission = {
                "version": {"major": 4, "minor": 3},
                "tests": tests,
            }

            if await self.submit_to_kcidb(submission):
                logger.info(f"Submitted {len(tests)} tests to KCIDB")
                for node in nodes:
                    self.processed_ids.add(node["id"])
                return len(tests)

        return 0

    async def run(self):
        await self.start()

        try:
            while True:
                try:
                    builds = await self.process_kbuilds()
                    tests = await self.process_tests()

                    if builds > 0 or tests > 0:
                        logger.info(f"Processed {builds} builds, {tests} tests")

                except Exception as e:
                    logger.error(f"Error in processing loop: {e}")

                await asyncio.sleep(POLL_INTERVAL)

        finally:
            await self.stop()


if __name__ == "__main__":
    bridge = KCIDBBridge()
    asyncio.run(bridge.run())
