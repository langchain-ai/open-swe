"""The GitHub App installation's hourly quotas, sampled every minute.

Everything acting as the installation spends the same quotas: the backend, MDA, and
every sandbox whose proxy carries an installation token. Only the backend's own
requests show in its request logs; these samples show the total, so the difference
is what MDA and the sandboxes spend.
"""

import logging
from datetime import UTC, datetime
from typing import Final

from openswe.github.app import GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID, GITHUB_APP_PRIVATE_KEY
from openswe.github.http import GitHubClient
from openswe.jobs.queue import Job

logger = logging.getLogger(__name__)

SAMPLE_JOB: Final = "github_quota_sample"
SAMPLE_SCHEDULE: Final = "* * * * *"
# The quotas Robin spends; GitHub also reports search, code scanning and others.
_RESOURCES: Final = ("core", "graphql")


class InstallationQuota:
    @staticmethod
    async def sample(_job: Job) -> None:
        """Log what is left of each quota, for dashboards and alerts on the installation."""
        # Without the App, a token is a developer's own ``gh`` login, not the installation's.
        if not (GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY and GITHUB_APP_INSTALLATION_ID):
            return
        async with GitHubClient.as_app() as github:
            quotas = await github.rate_limits()
        for resource in _RESOURCES:
            if (quota := quotas.get(resource)) is None:
                continue
            logger.info(
                "GitHub installation quota",
                extra={
                    "github_installation_id": GITHUB_APP_INSTALLATION_ID,
                    "github_quota_resource": resource,
                    "github_quota_limit": quota.limit,
                    "github_quota_used": quota.used,
                    "github_quota_remaining": quota.remaining,
                    "github_quota_reset_at": datetime.fromtimestamp(quota.reset, UTC).isoformat(),
                },
            )
