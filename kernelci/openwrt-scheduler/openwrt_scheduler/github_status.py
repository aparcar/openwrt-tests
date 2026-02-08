"""
GitHub Commit Status Integration

Posts commit statuses and PR comments for test results.
Provides feedback to developers when tests pass/fail.
"""

import logging
from typing import Literal

from github import Auth, Github
from github.GithubException import GithubException

from .config import settings

logger = logging.getLogger(__name__)

StatusState = Literal["pending", "success", "failure", "error"]


class GitHubStatusPoster:
    """
    Posts commit statuses and PR comments to GitHub.

    Provides test feedback to developers:
    - Commit status (pending/success/failure/error)
    - PR comments with detailed results
    """

    def __init__(
        self,
        repository: str | None = None,
        token: str | None = None,
    ):
        self.repository = repository or settings.github_repo
        self.token = token or settings.github_token
        self._github: Github | None = None

    def connect(self) -> None:
        """Initialize GitHub client."""
        if not self.token:
            logger.warning("No GitHub token configured, status posting disabled")
            return

        auth = Auth.Token(self.token)
        self._github = Github(auth=auth)

    def close(self) -> None:
        """Close GitHub client."""
        if self._github:
            self._github.close()
            self._github = None

    @property
    def github(self) -> Github | None:
        return self._github

    def post_status(
        self,
        commit_sha: str,
        state: StatusState,
        context: str = "OpenWrt Tests",
        description: str = "",
        target_url: str | None = None,
    ) -> bool:
        """
        Post a commit status to GitHub.

        Args:
            commit_sha: Full SHA of the commit
            state: Status state (pending, success, failure, error)
            context: Status context (appears as the check name)
            description: Short description (max 140 chars)
            target_url: URL to link to for details

        Returns:
            True if successful, False otherwise
        """
        if not self._github:
            logger.debug("GitHub not connected, skipping status post")
            return False

        try:
            repo = self._github.get_repo(self.repository)
            commit = repo.get_commit(commit_sha)

            # Truncate description to GitHub's limit
            if len(description) > 140:
                description = description[:137] + "..."

            commit.create_status(
                state=state,
                target_url=target_url or "",
                description=description,
                context=context,
            )

            logger.info(
                f"Posted status to {self.repository}@{commit_sha[:7]}: "
                f"{state} - {description}"
            )
            return True

        except GithubException as e:
            logger.error(f"Failed to post GitHub status: {e}")
            return False

    def post_pr_comment(
        self,
        pr_number: int,
        body: str,
    ) -> bool:
        """
        Post a comment on a pull request.

        Args:
            pr_number: PR number
            body: Comment body (markdown supported)

        Returns:
            True if successful, False otherwise
        """
        if not self._github:
            logger.debug("GitHub not connected, skipping PR comment")
            return False

        try:
            repo = self._github.get_repo(self.repository)
            pr = repo.get_pull(pr_number)
            pr.create_issue_comment(body)

            logger.info(f"Posted comment on PR #{pr_number}")
            return True

        except GithubException as e:
            logger.error(f"Failed to post PR comment: {e}")
            return False

    def post_test_results(
        self,
        commit_sha: str,
        passed: int,
        failed: int,
        skipped: int,
        target_url: str | None = None,
        pr_number: int | None = None,
        device: str | None = None,
        details: list[dict] | None = None,
    ) -> bool:
        """
        Post test results as commit status and optionally PR comment.

        Args:
            commit_sha: Commit SHA to post status on
            passed: Number of passed tests
            failed: Number of failed tests
            skipped: Number of skipped tests
            target_url: URL to full test results
            pr_number: PR number for detailed comment (optional)
            device: Device name tested on
            details: List of test details for PR comment

        Returns:
            True if successful
        """
        total = passed + failed + skipped
        state: StatusState = "success" if failed == 0 else "failure"

        # Build description
        device_str = f" on {device}" if device else ""
        description = f"{passed}/{total} tests passed{device_str}"

        # Build context with device name for multi-device testing
        context = "OpenWrt Tests"
        if device:
            context = f"OpenWrt Tests ({device})"

        # Post commit status
        success = self.post_status(
            commit_sha=commit_sha,
            state=state,
            context=context,
            description=description,
            target_url=target_url,
        )

        # Post detailed PR comment if requested and there are failures
        if pr_number and failed > 0 and details:
            comment = self._format_results_comment(
                passed=passed,
                failed=failed,
                skipped=skipped,
                device=device,
                target_url=target_url,
                details=details,
            )
            self.post_pr_comment(pr_number, comment)

        return success

    def _format_results_comment(
        self,
        passed: int,
        failed: int,
        skipped: int,
        device: str | None,
        target_url: str | None,
        details: list[dict],
    ) -> str:
        """Format a PR comment with test results."""
        status_emoji = "✅" if failed == 0 else "❌"

        lines = [
            f"## {status_emoji} Test Results",
            "",
            f"**Device:** {device or 'Unknown'}",
            f"**Results:** {passed} passed, {failed} failed, {skipped} skipped",
            "",
        ]

        if failed > 0:
            lines.append("### Failed Tests")
            lines.append("")
            for test in details:
                if test.get("status") in ("fail", "error"):
                    name = test.get("name", "unknown")
                    error = test.get("error_message", "No error message")
                    lines.append(f"- **{name}**")
                    if error:
                        lines.append("  ```")
                        lines.append(f"  {error[:500]}")
                        lines.append("  ```")
            lines.append("")

        if target_url:
            lines.append(f"[View full results]({target_url})")

        return "\n".join(lines)


# Global instance for convenience
_poster: GitHubStatusPoster | None = None


def get_github_poster() -> GitHubStatusPoster:
    """Get or create the global GitHub status poster."""
    global _poster
    if _poster is None:
        _poster = GitHubStatusPoster()
        _poster.connect()
    return _poster
