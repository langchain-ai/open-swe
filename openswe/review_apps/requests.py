"""User-requested app reviews, with the same gates and queue as preview events."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid7

from openswe import event_claims
from openswe.dashboard.settings_cache import cached_settings
from openswe.jobs import queue
from openswe.plugins.installed import InstalledPlugin
from openswe.plugins.manifest import PluginPermission
from openswe.plugins.records import PluginApps
from openswe.review_apps.outcomes import ReviewOutcome, ReviewOutcomes


@dataclass(frozen=True)
class PluginReviews:
    plugin: InstalledPlugin

    async def request(self, repository: str, number: int) -> ReviewOutcome:
        from openswe.github.webhook import PreviewReady

        self.plugin.require(PluginPermission.AGENT)
        async with self.plugin.github(repository) as client:
            info = await client.info()
            pull = await client.pull_request(number).pull()
        if info.get("private") is not True:
            raise PermissionError("App reviews are available only for private repositories")
        if pull.get("state") != "open":
            raise ValueError("Only open pull requests can request an app review")
        head = (pull.get("head") or {}).get("sha")
        if not isinstance(head, str) or not head:
            raise ValueError("GitHub returned no head commit")
        repository = repository.lower()
        owner, repo = repository.split("/", 1)
        config = {"owner": owner, "name": repo}
        settings = await cached_settings()
        app = self.plugin.installation.plugin
        approved = next(
            (
                release
                for release in await PluginApps.releases(settings.review_app_releases)
                if release.app == app
                and release.manifest.review is not None
                and release.manifest.review.browser
            ),
            None,
        )
        if approved is None:
            raise PermissionError("An administrator must enable this app in Robin's reviews")
        outcomes = ReviewOutcomes(app, repository, number)
        request_id = str(uuid7())
        checks = await PreviewReady.checks(config)
        if not checks:
            return await outcomes.put(
                ReviewOutcome(
                    id=request_id,
                    head_sha=head,
                    status="blocked",
                    reason="Enable automatic reviews and configure Preview checks in Robin Defaults.",
                )
            )
        check = sorted(checks)[0]
        key = PreviewReady.key(owner, repo, number, head, check)
        job_key = f"{PreviewReady.JOB}:{key}"
        existing = await queue.get(job_key)
        if existing is not None:
            previous_id = existing.payload.get("request_id")
            if isinstance(previous_id, str) and existing.payload.get("app") == str(app):
                previous = await outcomes.get(previous_id)
                if previous is not None:
                    return previous
            return ReviewOutcome(
                id=str(existing.id),
                head_sha=head,
                status="queued",
                reason="Capture work is already queued or running.",
            )
        group = f"{app}:{repository}#{number}:"
        limited = await event_claims.claim_within_limit(
            "app_review_requests",
            group + request_id,
            ttl=timedelta(hours=1),
            group=group,
            limit=3,
            within=timedelta(hours=1),
        )
        if limited == "limited":
            raise ValueError("Three capture requests were made in the last hour. Try again later.")
        await outcomes.queued(request_id, head, check)
        try:
            job = await queue.schedule(
                PreviewReady.JOB,
                {
                    "owner": owner,
                    "repo": repo,
                    "number": number,
                    "head_sha": head,
                    "check": check,
                    "repo_private": True,
                    "repo_id": info.get("id"),
                    "app": str(app),
                    "request_id": request_id,
                },
                key=job_key,
                replace=False,
                by=self.plugin.user.id,
            )
        except Exception:
            await outcomes.finish(
                request_id, head, "failed", "Could not queue capture work. Retry capture."
            )
            raise
        if job.payload.get("request_id") != request_id:
            await outcomes.delete(request_id)
        return ReviewOutcome(id=request_id, head_sha=head, status="queued")
