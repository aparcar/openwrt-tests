"""
Test Repository Sync

Fetches tests from a git repository before job execution.
If the repository already exists locally, updates it with git pull.

Similar to LAVA pattern where tests are fetched at job execution time.
See: https://docs.lavasoftware.org/lava/writing-tests.html
"""

import asyncio
import logging
import shutil
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)


async def _run_git(*args: str, cwd: Path | None = None) -> tuple[int, str]:
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


async def ensure_tests(
    repo_url: str | None = None,
    branch: str | None = None,
    dest_dir: Path | None = None,
    subdir: str | None = None,
) -> Path:
    """
    Ensure tests are available and up-to-date before job execution.

    If repo_url is provided, clones or updates from that repository.
    If repo already exists locally, pulls latest changes.
    If no repo_url and no local repo, uses dest_dir as-is (local tests).

    Args:
        repo_url: Git repository URL (optional, uses settings if not provided)
        branch: Branch to checkout (optional, uses settings if not provided)
        dest_dir: Destination directory (optional, uses settings if not provided)
        subdir: Subdirectory within repo containing tests (optional)

    Returns:
        Path to the tests directory (including subdir if specified)
    """
    repo_url = repo_url or settings.tests_repo_url
    branch = branch or settings.tests_repo_branch
    dest_dir = dest_dir or settings.tests_dir
    subdir = subdir if subdir is not None else settings.tests_repo_subdir

    dest_dir.parent.mkdir(parents=True, exist_ok=True)

    # Helper to get final tests path including subdir
    def _tests_path() -> Path:
        if subdir:
            return dest_dir / subdir
        return dest_dir

    # If no repo URL configured, just use local directory
    if not repo_url:
        tests_path = _tests_path()
        if not tests_path.exists():
            raise RuntimeError(
                f"Tests directory {tests_path} does not exist "
                "and no TESTS_REPO_URL configured"
            )
        logger.debug(f"Using local tests at {tests_path}")
        return tests_path

    # Check if already cloned
    if (dest_dir / ".git").exists():
        # Update existing repository
        logger.debug(f"Updating tests in {dest_dir}")
        returncode, output = await _run_git("fetch", "origin", branch, cwd=dest_dir)
        if returncode != 0:
            logger.warning(f"Git fetch failed: {output}")
            # Continue with existing checkout
            return _tests_path()

        # Check if there are updates
        _, local_rev = await _run_git("rev-parse", "HEAD", cwd=dest_dir)
        _, remote_rev = await _run_git("rev-parse", f"origin/{branch}", cwd=dest_dir)

        if local_rev != remote_rev:
            await _run_git("reset", "--hard", f"origin/{branch}", cwd=dest_dir)
            logger.info(f"Tests updated: {local_rev[:8]} -> {remote_rev[:8]}")
        else:
            logger.debug("Tests already up-to-date")
    else:
        # Clone fresh
        logger.info(f"Cloning tests from {repo_url}")
        if dest_dir.is_symlink():
            dest_dir.unlink()
        elif dest_dir.exists():
            shutil.rmtree(dest_dir)

        returncode, output = await _run_git(
            "clone", "--branch", branch, "--depth", "1", repo_url, str(dest_dir)
        )
        if returncode != 0:
            raise RuntimeError(f"Failed to clone tests: {output}")
        logger.info(f"Tests cloned to {dest_dir}")

    tests_path = _tests_path()
    if subdir and not tests_path.exists():
        raise RuntimeError(
            f"Subdirectory '{subdir}' does not exist in repository"
        )
    return tests_path
