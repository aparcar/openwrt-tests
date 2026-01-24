"""
Notification Manager for Health Check System

Handles notifications for device health events:
- GitHub issue creation/closure
- Email notifications (optional)
- Slack/webhook notifications (optional)
"""

import logging
from datetime import datetime
from typing import Any

from github import Auth, Github
from github.Issue import Issue

from ..config import settings
from ..models import Device, DeviceStatus

logger = logging.getLogger(__name__)


class NotificationManager:
    """
    Manages notifications for device health events.

    Supports:
    - GitHub issues for device failures
    - Auto-closing issues when devices recover
    - Optional email and Slack notifications
    """

    def __init__(self, config: dict):
        """
        Initialize notification manager.

        Args:
            config: Notification configuration from pipeline.yaml
        """
        self.config = config
        self._github: Github | None = None
        self._issue_cache: dict[str, int] = {}  # device_id -> issue_number

        # GitHub configuration
        self.github_config = config.get("github_issues", {})
        self.github_enabled = self.github_config.get("enabled", False)
        self.github_repo = self.github_config.get("repository", settings.github_repo)
        self.github_labels = self.github_config.get(
            "labels", ["device-failure", "health-check"]
        )
        self.auto_close = self.github_config.get("auto_close", True)

    def initialize(self) -> None:
        """Initialize notification clients."""
        if self.github_enabled and settings.github_token:
            auth = Auth.Token(settings.github_token)
            self._github = Github(auth=auth)
            logger.info(f"GitHub notifications enabled for {self.github_repo}")
        else:
            logger.info("GitHub notifications disabled")

    def cleanup(self) -> None:
        """Cleanup resources."""
        if self._github:
            self._github.close()

    @property
    def github(self) -> Github:
        """Get GitHub client."""
        if self._github is None:
            raise RuntimeError("GitHub client not initialized")
        return self._github

    async def notify_device_failure(
        self,
        device: Device,
        error_message: str | None = None,
        console_log_url: str | None = None,
    ) -> None:
        """
        Send notifications for a device failure.

        Creates GitHub issue if threshold reached and enabled.
        """
        logger.info(
            f"Device failure notification",
            device_id=device.id,
            failures=device.consecutive_failures,
        )

        # Create GitHub issue if enabled and device is disabled
        if self.github_enabled and device.status == DeviceStatus.DISABLED:
            await self._create_github_issue(device, error_message, console_log_url)

    async def notify_device_recovery(self, device: Device) -> None:
        """
        Send notifications for device recovery.

        Closes GitHub issue if auto_close is enabled.
        """
        logger.info(f"Device recovery notification", device_id=device.id)

        # Close GitHub issue if exists
        if self.github_enabled and self.auto_close:
            await self._close_github_issue(device)

    async def _create_github_issue(
        self,
        device: Device,
        error_message: str | None = None,
        console_log_url: str | None = None,
    ) -> Issue | None:
        """Create a GitHub issue for a failing device."""
        if not self._github:
            return None

        # Check if issue already exists
        existing = await self._find_existing_issue(device.id)
        if existing:
            logger.debug(f"Issue already exists for {device.id}: #{existing.number}")
            return existing

        try:
            repo = self.github.get_repo(self.github_repo)

            title = f"[Health Check] {device.id} failing - disabled"

            body = self._format_issue_body(device, error_message, console_log_url)

            issue = repo.create_issue(
                title=title,
                body=body,
                labels=self.github_labels,
            )

            # Cache the issue number
            self._issue_cache[device.id] = issue.number

            logger.info(f"Created GitHub issue #{issue.number} for {device.id}")
            return issue

        except Exception as e:
            logger.error(f"Failed to create GitHub issue: {e}")
            return None

    async def _close_github_issue(self, device: Device) -> None:
        """Close an existing GitHub issue for a recovered device."""
        if not self._github:
            return

        try:
            # Find the issue
            issue = await self._find_existing_issue(device.id)
            if not issue:
                logger.debug(f"No open issue found for {device.id}")
                return

            # Add recovery comment
            issue.create_comment(
                f"Device `{device.id}` has recovered and passed health check.\n\n"
                f"- **Recovery time:** {datetime.utcnow().isoformat()}\n"
                f"- **Status:** Healthy\n\n"
                f"Closing issue automatically."
            )

            # Close the issue
            issue.edit(state="closed")

            # Remove from cache
            self._issue_cache.pop(device.id, None)

            logger.info(f"Closed GitHub issue #{issue.number} for {device.id}")

        except Exception as e:
            logger.error(f"Failed to close GitHub issue: {e}")

    async def _find_existing_issue(self, device_id: str) -> Issue | None:
        """Find an existing open issue for a device."""
        # Check cache first
        if device_id in self._issue_cache:
            try:
                repo = self.github.get_repo(self.github_repo)
                issue = repo.get_issue(self._issue_cache[device_id])
                if issue.state == "open":
                    return issue
                else:
                    # Issue was closed externally
                    del self._issue_cache[device_id]
            except Exception:
                del self._issue_cache[device_id]

        # Search for open issues
        try:
            repo = self.github.get_repo(self.github_repo)

            # Search by title
            query = f"repo:{self.github_repo} is:issue is:open {device_id} in:title"
            issues = self.github.search_issues(query=query)

            for issue in issues:
                if device_id in issue.title:
                    self._issue_cache[device_id] = issue.number
                    return issue

        except Exception as e:
            logger.warning(f"Error searching for issues: {e}")

        return None

    def _format_issue_body(
        self,
        device: Device,
        error_message: str | None = None,
        console_log_url: str | None = None,
    ) -> str:
        """Format the GitHub issue body."""
        body = f"""## Device Health Check Failure

**Device:** `{device.id}`
**Lab:** {device.lab_name}
**Target:** {device.target}/{device.subtarget}
**Last Check:** {datetime.utcnow().isoformat()}
**Consecutive Failures:** {device.consecutive_failures}

### Error Details

```
{error_message or 'No error message available'}
```

"""

        if console_log_url:
            body += f"""### Console Log

[View console log]({console_log_url})

"""

        body += """### Actions Taken

- Device has been **disabled** from the test pool
- No new test jobs will be scheduled for this device

### Resolution Steps

1. Investigate the device manually
2. Check physical connections (power, serial, network)
3. Verify device is accessible via labgrid
4. Fix any hardware/network issues
5. Run manual health check via API:
   ```
   POST /api/v1/devices/{device_id}/health-check
   ```
6. Device will be re-enabled after successful health check

### Labels

"""
        for label in self.github_labels:
            body += f"- `{label}`\n"

        return body

    async def send_summary_notification(
        self,
        summary: dict[str, Any],
    ) -> None:
        """
        Send a summary notification of health check results.

        This could be a daily digest or post-check summary.
        """
        logger.info(
            f"Health check summary",
            total=summary.get("total", 0),
            healthy=summary.get("healthy", 0),
            failing=summary.get("failing", 0),
            disabled=summary.get("disabled", 0),
        )

        # TODO: Implement email/Slack summary notifications
