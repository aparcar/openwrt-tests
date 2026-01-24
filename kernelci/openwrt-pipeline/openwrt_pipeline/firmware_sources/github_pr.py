"""
GitHub Pull Request firmware source.

Watches GitHub PRs for firmware artifacts from CI builds.
Supports triggering tests on PRs with specific labels.
"""

import hashlib
import io
import logging
import zipfile
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

import httpx
from github import Auth, Github
from github.PullRequest import PullRequest
from github.WorkflowRun import WorkflowRun

from ..config import settings
from ..models import Firmware, FirmwareArtifacts, FirmwareSource as FirmwareSourceEnum
from .base import FirmwareSource

logger = logging.getLogger(__name__)


class GitHubPRSource(FirmwareSource):
    """
    Firmware source for GitHub Pull Request CI artifacts.

    Monitors PRs with specific labels and extracts firmware
    artifacts from successful workflow runs.
    """

    def __init__(self, name: str, config: dict):
        super().__init__(name, config)
        self.repository = config.get("repository", "openwrt/openwrt")
        self.trigger_labels = config.get("trigger_labels", ["ci-test-requested"])
        self.workflow_name = config.get("workflow_name", "Build")
        self.artifact_pattern = config.get("artifact_pattern", "openwrt-*")
        self._github: Github | None = None
        self._http_client: httpx.AsyncClient | None = None

    async def initialize(self) -> None:
        """Initialize GitHub client."""
        await super().initialize()

        token = self.config.get("token") or settings.github_token
        if not token:
            logger.warning("No GitHub token configured, PR source will be limited")
            self.enabled = False
            return

        auth = Auth.Token(token)
        self._github = Github(auth=auth)
        self._http_client = httpx.AsyncClient(
            headers={"Authorization": f"token {token}"},
            timeout=httpx.Timeout(60.0),
            follow_redirects=True,
        )

    async def cleanup(self) -> None:
        """Close clients."""
        if self._github:
            self._github.close()
        if self._http_client:
            await self._http_client.aclose()
        await super().cleanup()

    @property
    def github(self) -> Github:
        """Get GitHub client."""
        if self._github is None:
            raise RuntimeError("Source not initialized or no token configured")
        return self._github

    @property
    def client(self) -> httpx.AsyncClient:
        """Get HTTP client."""
        if self._http_client is None:
            raise RuntimeError("Source not initialized")
        return self._http_client

    async def scan(self) -> AsyncIterator[Firmware]:
        """
        Scan PRs with trigger labels for firmware artifacts.

        Yields firmware for each PR that:
        1. Has a trigger label
        2. Has a successful workflow run
        3. Has firmware artifacts
        """
        if not self.enabled:
            return

        logger.info(f"Scanning GitHub PRs in {self.repository}")

        repo = self.github.get_repo(self.repository)

        # Get open PRs with trigger labels
        for label in self.trigger_labels:
            try:
                pulls = repo.get_pulls(state="open")
                for pr in pulls:
                    pr_labels = [l.name for l in pr.labels]
                    if label in pr_labels:
                        async for firmware in self._process_pr(pr):
                            yield firmware
            except Exception as e:
                logger.error(f"Error scanning PRs with label '{label}': {e}")

    async def _process_pr(self, pr: PullRequest) -> AsyncIterator[Firmware]:
        """Process a single PR for firmware artifacts."""
        logger.info(f"Processing PR #{pr.number}: {pr.title}")

        # Find successful workflow runs for this PR
        try:
            runs = pr.head.repo.get_workflow_runs(
                branch=pr.head.ref,
                status="success",
            )
        except Exception as e:
            logger.error(f"Error getting workflow runs for PR #{pr.number}: {e}")
            return

        # Get the most recent successful run
        latest_run: WorkflowRun | None = None
        for run in runs:
            if run.name == self.workflow_name or self.workflow_name in (run.name or ""):
                latest_run = run
                break

        if not latest_run:
            logger.debug(f"No successful workflow runs found for PR #{pr.number}")
            return

        # Get artifacts from the run
        try:
            artifacts = latest_run.get_artifacts()
        except Exception as e:
            logger.error(f"Error getting artifacts for run {latest_run.id}: {e}")
            return

        for artifact in artifacts:
            # Check if artifact matches our pattern
            if not self._matches_pattern(artifact.name, self.artifact_pattern):
                continue

            # Parse target info from artifact name
            target_info = self._parse_artifact_name(artifact.name)
            if not target_info:
                continue

            firmware = self._create_firmware(
                pr=pr,
                run=latest_run,
                artifact=artifact,
                target_info=target_info,
            )
            if firmware:
                yield firmware

    def _matches_pattern(self, name: str, pattern: str) -> bool:
        """Check if artifact name matches pattern (simple glob)."""
        if pattern.endswith("*"):
            return name.startswith(pattern[:-1])
        return name == pattern

    def _parse_artifact_name(self, name: str) -> dict | None:
        """
        Parse target/subtarget from artifact name.

        Expected format: openwrt-{target}-{subtarget}[-optional]
        Example: openwrt-ath79-generic, openwrt-mediatek-filogic
        """
        parts = name.split("-")
        if len(parts) < 3 or parts[0] != "openwrt":
            return None

        return {
            "target": parts[1],
            "subtarget": parts[2] if len(parts) > 2 else "generic",
        }

    def _create_firmware(
        self,
        pr: PullRequest,
        run: WorkflowRun,
        artifact,
        target_info: dict,
    ) -> Firmware | None:
        """Create a Firmware object for a PR artifact."""
        firmware_id = self._generate_firmware_id(
            pr_number=pr.number,
            target=target_info["target"],
            subtarget=target_info["subtarget"],
            commit=pr.head.sha,
        )

        return Firmware(
            id=firmware_id,
            source=FirmwareSourceEnum.PR,
            source_url=pr.html_url,
            source_ref=f"PR #{pr.number}",
            version=f"pr-{pr.number}",
            target=target_info["target"],
            subtarget=target_info["subtarget"],
            profile="*",  # PR builds may contain multiple profiles
            git_repository_url=pr.base.repo.clone_url,
            git_commit_hash=pr.head.sha,
            git_branch=pr.head.ref,
            artifacts=FirmwareArtifacts(),  # Will be populated on download
            description=f"PR #{pr.number}: {pr.title}",
            build_time=run.created_at,
        )

    def _generate_firmware_id(
        self,
        pr_number: int,
        target: str,
        subtarget: str,
        commit: str,
    ) -> str:
        """Generate a unique firmware ID for a PR."""
        hash_input = f"pr:{pr_number}:{target}:{subtarget}:{commit}"
        short_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
        return f"openwrt:pr-{pr_number}:{target}:{subtarget}:{short_hash}"

    async def download_artifact(
        self,
        firmware: Firmware,
        artifact_type: str,
        destination: str,
    ) -> str:
        """
        Download PR artifact and extract firmware files.

        GitHub artifacts are ZIP files containing the actual firmware.
        """
        # For PR artifacts, we need to download the whole artifact ZIP
        # and extract the relevant firmware file
        raise NotImplementedError(
            "PR artifact download requires artifact_id. "
            "Use download_pr_artifact() directly."
        )

    async def download_pr_artifact(
        self,
        pr_number: int,
        artifact_id: int,
        destination: str,
    ) -> dict[str, str]:
        """
        Download and extract a PR artifact ZIP.

        Args:
            pr_number: Pull request number
            artifact_id: GitHub artifact ID
            destination: Directory to extract files to

        Returns:
            Dict mapping firmware types to file paths
        """
        repo = self.github.get_repo(self.repository)

        # Get artifact download URL
        artifact = repo.get_artifact(artifact_id)
        download_url = artifact.archive_download_url

        logger.info(f"Downloading artifact {artifact.name} from PR #{pr_number}")

        # Download the ZIP file
        response = await self.client.get(download_url)
        response.raise_for_status()

        # Extract to destination
        dest_path = Path(destination)
        dest_path.mkdir(parents=True, exist_ok=True)

        extracted_files = {}
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            for file_info in zf.filelist:
                if file_info.is_dir():
                    continue

                filename = Path(file_info.filename).name
                file_type = self._detect_firmware_type(filename)

                if file_type:
                    extract_path = dest_path / filename
                    with open(extract_path, "wb") as f:
                        f.write(zf.read(file_info.filename))
                    extracted_files[file_type] = str(extract_path)
                    logger.info(f"Extracted {file_type}: {extract_path}")

        return extracted_files

    def _detect_firmware_type(self, filename: str) -> str | None:
        """Detect firmware type from filename."""
        filename_lower = filename.lower()

        if "sysupgrade" in filename_lower:
            return "sysupgrade"
        elif "factory" in filename_lower:
            return "factory"
        elif "initramfs" in filename_lower or "kernel" in filename_lower:
            return "initramfs"
        elif filename_lower.endswith((".bin", ".img", ".itb")):
            return "unknown"

        return None

    async def add_pr_comment(
        self,
        pr_number: int,
        comment: str,
    ) -> None:
        """Add a comment to a PR with test results."""
        repo = self.github.get_repo(self.repository)
        pr = repo.get_pull(pr_number)
        pr.create_issue_comment(comment)

    async def update_pr_status(
        self,
        pr_number: int,
        sha: str,
        state: str,
        context: str,
        description: str,
        target_url: str | None = None,
    ) -> None:
        """Update PR commit status."""
        repo = self.github.get_repo(self.repository)
        commit = repo.get_commit(sha)
        commit.create_status(
            state=state,
            context=context,
            description=description,
            target_url=target_url,
        )
