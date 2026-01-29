"""
Test Repository Sync

Manages test repository synchronization. Supports two modes:

1. **Static sync** (TESTS_REPO_URL configured): Clones/pulls tests on startup
   and periodically. Simple setup for labs running a fixed set of tests.

2. **Per-job sync** (job includes tests_repo): Fetches tests specified in
   the job definition. Follows the LAVA pattern where tests are fetched
   at job execution time.

See: https://docs.lavasoftware.org/lava/writing-tests.html
"""

import asyncio
import logging
import shutil
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)


async def run_git(*args: str, cwd: Path | None = None) -> tuple[int, str]:
    """Run a git command asynchronously."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    return proc.returncode, stdout.decode().strip()


async def fetch_tests_for_job(
    repo_url: str,
    branch: str = "main",
    dest_dir: Path | None = None,
) -> Path:
    """
    Fetch tests from a git repository for a specific job.

    This follows the LAVA pattern where tests are fetched at job
    execution time from a URL specified in the job definition.

    Args:
        repo_url: Git repository URL
        branch: Branch to checkout
        dest_dir: Destination directory (auto-generated if None)

    Returns:
        Path to the cloned tests directory
    """
    if dest_dir is None:
        # Generate unique directory based on repo URL hash
        import hashlib

        repo_hash = hashlib.sha256(f"{repo_url}:{branch}".encode()).hexdigest()[:12]
        dest_dir = settings.tests_dir / f"job-{repo_hash}"

    dest_dir.parent.mkdir(parents=True, exist_ok=True)

    # Check if already cloned
    if (dest_dir / ".git").exists():
        # Pull updates
        returncode, output = await run_git("fetch", "origin", branch, cwd=dest_dir)
        if returncode == 0:
            await run_git("reset", "--hard", f"origin/{branch}", cwd=dest_dir)
        logger.debug(f"Updated tests in {dest_dir}")
    else:
        # Clone fresh
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        returncode, output = await run_git(
            "clone", "--branch", branch, "--depth", "1", repo_url, str(dest_dir)
        )
        if returncode != 0:
            raise RuntimeError(f"Failed to clone tests: {output}")
        logger.info(f"Cloned tests to {dest_dir}")

    return dest_dir


class TestSync:
    """
    Synchronizes test files from a git repository (static mode).

    Used when TESTS_REPO_URL is configured. Clones/pulls tests on startup
    and periodically checks for updates.

    For per-job test fetching, use fetch_tests_for_job() instead.
    """

    def __init__(self):
        self.repo_url = settings.tests_repo_url
        self.branch = settings.tests_repo_branch
        self.tests_dir = settings.tests_dir
        self.sync_interval = settings.tests_sync_interval
        self._running = False

    @property
    def enabled(self) -> bool:
        """Check if static sync is enabled (repo URL configured)."""
        return bool(self.repo_url)

    async def initialize(self) -> None:
        """Initial sync on startup."""
        if not self.enabled:
            logger.info(
                "Static test sync disabled. Tests will be fetched per-job "
                "or must exist locally at %s",
                self.tests_dir,
            )
            return

        logger.info(f"Syncing tests from {self.repo_url}")
        await self._sync()

    async def _sync(self) -> bool:
        """Sync tests from remote repository."""
        if not self.enabled:
            return True

        try:
            await fetch_tests_for_job(
                repo_url=self.repo_url,
                branch=self.branch,
                dest_dir=self.tests_dir,
            )
            return True
        except Exception as e:
            logger.exception(f"Test sync failed: {e}")
            return False

    async def run(self) -> None:
        """Periodic sync loop."""
        if not self.enabled:
            return

        self._running = True
        logger.info(f"Starting test sync loop (interval: {self.sync_interval}s)")

        while self._running:
            await asyncio.sleep(self.sync_interval)
            if self._running:
                await self._sync()

    def stop(self) -> None:
        """Stop the sync loop."""
        self._running = False
