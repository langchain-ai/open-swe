"""What reviews saved to apps for a pull request, deleted once it closes.

Kept apart from ``apps``, which reaches the agent's sandboxes, so the web app's GitHub
channel can run it without importing the agent stack.
"""

import logging
from uuid import UUID

from openswe.github.pull_requests import PullRequestEvent
from openswe.github.repo_names import normalize_repo_full_name
from openswe.plugins.records import DocumentScope, PluginApps, PluginDocuments

logger = logging.getLogger(__name__)


class ReviewAppData:
    """What reviews wrote to apps' storage for pull requests."""

    @staticmethod
    async def delete_on_close(payload: object) -> int:
        """When a pull request closes, deletes what reviews saved to apps for it, with the
        documents' files, and returns how many documents went.

        That is each document under the pull request's ``pulls/<number>/`` in a collection
        a review step of any of the app's releases writes, that no person created or changed:
        what the app's people keep there, or wrote before the collection became the review's,
        stays.
        """
        event = PullRequestEvent.parse(payload)
        identity = event.identity if event is not None else None
        if identity is None:
            return 0
        owner, repo, number = identity
        scope = DocumentScope(repository=normalize_repo_full_name(f"{owner}/{repo}").lower())
        prefix = f"pulls/{number}/"
        deleted = 0
        writes: dict[UUID, frozenset[str]] = {}
        for app, collection in await PluginDocuments.collections_under(scope, prefix):
            if app not in writes:
                writes[app] = await PluginApps.review_writes(app)
            if collection in writes[app]:
                deleted += await PluginDocuments(app).delete_unattended_under(
                    collection, scope, prefix
                )
        if deleted:
            logger.info(
                "Review app data of a closed pull request deleted",
                extra={"repository": scope.repository, "pr_number": number, "count": deleted},
            )
        return deleted
