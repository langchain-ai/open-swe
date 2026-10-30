"""Expedited review nomination shared by dashboard requests and agent tools."""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from openswe.audit_logs.context import current_audit_log
from openswe.audit_logs.models import ExpeditedExclusions
from openswe.expedited_review.eligibility import (
    MAX_CHANGED_LINES,
    MAX_FILES,
    ChangedFile,
    ExcludedHunk,
    Exclusion,
    Ineligible,
    assess_eligibility,
    fingerprint_matches,
)
from openswe.expedited_review.readiness import latest_review_states
from openswe.github.codeowners import CodeOwners
from openswe.github.comments import derive_pr_state
from openswe.github.http import GitHubClient, RepoFileUnreadableError, or_none
from openswe.github.pull_requests import PullRequest, PullRequestPayload
from openswe.github.repo_files import RepoSettings
from openswe.human_review.lifecycle import ReviewCard
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.standard import Origin, summary_line
from openswe.review.approvals import APPROVALS_PATH, fetch_approvals_md
from openswe.slack.channels import SlackChannel
from openswe.slack.client import GitHubPrRef
from openswe.slack.dm import send_dm
from openswe.slack.http import SlackRequestError
from openswe.users import User
from openswe.utils.json_types import JsonObject


class ExpeditedReviewIneligible(Exception):
    """The diff or author needs the standard review workflow instead."""


@dataclass(frozen=True, slots=True)
class ExpeditedReviewNomination:
    pr_ref: GitHubPrRef
    origin: Origin
    token: str

    def failure(self, error: str) -> JsonObject:
        return {"success": False, "error": error}

    def next_step(self, *, reused: bool, elsewhere: bool, in_thread: bool, over_mcp: bool) -> str:
        """What the agent should do once the card is up."""
        if over_mcp:
            return (
                "This diff already has an open card; no new one was posted. "
                if reused
                else "The approval card is posted. "
            ) + "Once someone approves, Open SWE starts a thread that merges it. Do not poll."
        if elsewhere:
            return (
                "This diff already has a card, so it stays in the Slack thread named here, not "
                'the channel you asked for. Cancel with action="cancel" and ask again to move it.'
            )
        posted = (
            "This diff already has an open card; no new one was posted."
            if reused
            else "The approval card is posted in the Slack thread."
        )
        posted += (
            " An approval goes to GitHub as the voter's review when they click. Call "
            "`merge_expedited_pr` once checks and reviews are "
            "clean; keep a `/baby-sit` watch on the PR so you are woken when they are. You are "
            "also woken when someone approves the card. Do not poll."
        )
        if reused or not in_thread:
            return posted
        return (
            f"{posted} The card is this turn's reply to the person who asked: finish with "
            "`slack_no_reply_needed` rather than a message announcing the pull request or "
            "this request."
        )

    async def discard(self, approval: HumanReviewRequest) -> None:
        async with HumanReviewRequest.locked(approval.id) as (session, row):
            if row is not None:
                await session.delete(row)

    async def resolve_exclusions(
        self,
        excluded: list[Exclusion],
        files: list[ChangedFile],
        pr_ref: GitHubPrRef,
        payload: PullRequestPayload,
        token: str,
    ) -> list[ExcludedHunk]:
        """Resolve against the target repository's APPROVALS.md on its default branch, and audit them."""
        approvals = await fetch_approvals_md(pr_ref.owner, pr_ref.repo, None, token=token)
        if approvals is None:
            raise ValueError(
                f"Nothing can be excluded: `{pr_ref.owner}/{pr_ref.repo}` has no `{APPROVALS_PATH}` "
                "on its default branch."
            )
        audited = ExpeditedExclusions(
            base_sha=payload.base_sha,
            head_sha=payload.head_sha,
            approvals_md_sha256=hashlib.sha256(approvals.encode()).hexdigest(),
            requested=excluded,
        )
        entry = current_audit_log.get()
        if entry is not None:
            entry.enrichments.expedited_exclusions = audited
            stored = await PullRequest.get(pr_ref.owner, pr_ref.repo, pr_ref.number)
            if stored is not None:
                self.audit_pull_request(stored.id)
        unique: dict[tuple[str, str, str], ExcludedHunk] = {}
        for exclusion in excluded:
            for hunk in exclusion.resolve(files):
                unique.setdefault((hunk["path"], hunk["header"], hunk["digest"]), hunk)
        audited.hunks = list(unique.values())
        return audited.hunks

    def audit_pull_request(self, pull_request_id: UUID) -> None:
        """The row may first exist once the card is saved, so the audit entry learns its id then."""
        entry = current_audit_log.get()
        if entry is None or entry.enrichments.expedited_exclusions is None:
            return
        entry.enrichments.expedited_exclusions.pull_request_id = pull_request_id
        entry.enrichments.resource_ids = [str(pull_request_id)]

    async def start(
        self,
        channel_id: str,
        thread_ts: str = "",
        inline_summary: str = "",
        excluded: list[Exclusion] | None = None,
        *,
        replace_standard: bool = True,
    ) -> JsonObject:
        pr_ref, origin, token = self.pr_ref, self.origin, self.token
        requester, thread_id = origin.requester, origin.thread_id
        own_channel, own_thread = origin.slack_channel_id, origin.slack_thread_ts
        async with GitHubClient.connect(token=token) as github:
            pull = github.repo(pr_ref.owner, pr_ref.repo).pull_request(pr_ref.number)
            pr = await or_none(pull.pull())
            files = await ChangedFile.of_pull(pull) if pr else None
            if not replace_standard and pr:
                if files is None or isinstance(assess_eligibility(files), Ineligible):
                    raise ExpeditedReviewIneligible("The diff needs standard review.")
                if (
                    await User.for_login("github", PullRequestPayload.model_validate(pr).author)
                    is None
                ):
                    raise ExpeditedReviewIneligible("The author needs standard review.")
            if pr and pr.get("state") == "open" and files is not None and len(files) < MAX_FILES:
                payload = PullRequestPayload.model_validate(pr)
                states = await latest_review_states(pull, payload.author)
                if states is None:
                    return self.failure("Could not read the pull request's reviews")
                approvers = {login for login, state in states.items() if state == "APPROVED"}
                if approvers and "CHANGES_REQUESTED" not in states.values():
                    try:
                        codeowners = await CodeOwners.fetch(
                            pull.repo, payload.base_ref or None, strict=True
                        )
                    except RepoFileUnreadableError:
                        return self.failure(
                            "Could not confirm the pull request's codeowner approvals"
                        )
                    if codeowners is None or await codeowners.approved_by(
                        [file.filename for file in files], approvers
                    ):
                        return self.failure(
                            "This pull request is already fully reviewed on GitHub, including all "
                            "code owners. No expedited review is needed; do not request another review."
                        )
        if not pr:
            return self.failure("Pull request is unavailable")
        if pr.get("state") != "open":
            return self.failure("Pull request is not open")
        head = pr.get("head") if isinstance(pr.get("head"), Mapping) else {}
        head_sha = head.get("sha") if isinstance(head, Mapping) else None
        if not isinstance(head_sha, str) or not head_sha:
            return self.failure("Pull request head SHA is unavailable")

        if files is None:
            return self.failure("Could not read the pull request's changed files")
        payload = PullRequestPayload.model_validate(pr)
        exclusions: list[ExcludedHunk] = []
        if excluded:
            try:
                exclusions = await self.resolve_exclusions(excluded, files, pr_ref, payload, token)
            except ValueError as exc:
                return self.failure(str(exc))
        verdict = assess_eligibility(files, exclusions)
        if isinstance(verdict, Ineligible):
            raise ExpeditedReviewIneligible(
                f"Not eligible for expedited review: {verdict.reason}. "
                f"Eligible changes touch at most {MAX_CHANGED_LINES} lines outside tests and "
                "exclusions, and every one of those files has to have a readable text diff. Test "
                f"files are not counted, and `excluded` leaves out hunks that qualify under the "
                f"target repository's `{APPROVALS_PATH}`. Otherwise ask for a normal review."
            )

        if await User.for_login("github", payload.author) is None:
            raise ExpeditedReviewIneligible(
                "Expedited review is only available for PRs authored by Open SWE users."
            )
        settings = await RepoSettings.cached(pr_ref.owner, pr_ref.repo)
        review_channel = settings.channel_for_files([file.filename for file in files])
        broadcast_target = await SlackChannel.resolve(review_channel) if review_channel else None
        if review_channel and broadcast_target is None:
            return self.failure(
                f"The configured review channel {review_channel!r} is unavailable to the bot. "
                "Invite the bot to that channel or update `reviewChannel` in `.open-swe/settings.json`."
            )
        broadcast_choice = (
            [{"id": broadcast_target.id, "name": broadcast_target.name}]
            if broadcast_target is not None and broadcast_target.name
            else []
        )
        active = await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
        displaced: HumanReviewRequest | None = None
        if active is not None and active.kind != "expedited":
            if not replace_standard:
                raise ExpeditedReviewIneligible("This pull request already has a review request.")
            displaced, active = active, None
        if active is not None and (
            active.requested_by_user_id != requester.id
            if requester is not None
            else bool(active.thread_id) and active.thread_id != thread_id
        ):
            return self.failure("This pull request's expedited review belongs to someone else")
        if (
            active is not None
            and fingerprint_matches(files, active.diff_fingerprint)
            and {(e["path"], e["digest"]) for e in active.excluded_hunks}
            == {(e["path"], e["digest"]) for e in exclusions}
        ):
            self.audit_pull_request(active.pull_request_id)
            if active.awaiting_ready and not payload.draft:
                updated = await HumanReviewRequest.transition(
                    active.id, expected=("open",), awaiting_ready=False
                )
                if updated is not None:
                    active = updated
            card = ReviewCard(active)
            if not active.awaiting_ready:
                await card.refresh()
                await card.broadcast_configured()
            readiness_warning = await card.prompt_author_ready()
            return {
                "success": True,
                "readiness_warning": readiness_warning,
                "approval_id": str(active.id),
                "pr_url": pr_ref.url,
                "head_sha": head_sha,
                "approvers": active.approvers,
                "completion_reply_required": active.awaiting_ready,
                "slack_channel_id": active.slack_channel_id,
                "next": readiness_warning
                or (
                    "The full draft card is in the author's DM; no thread card is posted until ready."
                    if active.awaiting_ready
                    else self.next_step(
                        reused=True,
                        elsewhere=active.slack_channel_id != channel_id,
                        in_thread=False,
                        over_mcp=requester is not None,
                    )
                ),
            }
        if active is not None:
            await ReviewCard(active).retire("superseded", "Replaced by a card for the newer diff.")

        pull_request = await PullRequest.load(pr_ref.owner, pr_ref.repo, pr_ref.number)
        pull_request.title = payload.title
        pull_request.body = payload.body or ""
        pull_request.state = derive_pr_state(
            state=payload.state, merged=payload.merged, draft=payload.draft
        )
        pull_request.head_ref = payload.head_ref
        pull_request.base_ref = payload.base_ref
        pull_request.author = payload.author
        pull_request.author_github_id = payload.author_id
        pull_request = await pull_request.save()
        if thread_id:
            pull_request = await pull_request.link_thread(thread_id, source="expedited_review")
        self.audit_pull_request(pull_request.id)
        # One open request per PR, so the displaced one closes before this row is written;
        # it is reopened below if the expedited card cannot be posted.
        if displaced is not None and (
            await ReviewCard(displaced).retire("superseded", "replaced by an expedited review")
            is None
        ):
            return self.failure("The pull request's review request changed meanwhile. Try again.")
        approval = await HumanReviewRequest(
            pull_request_id=pull_request.id,
            thread_id=thread_id,
            head_sha=head_sha,
            kind="expedited",
            diff_fingerprint=verdict.fingerprint,
            excluded_hunks=exclusions,
            tldr=summary_line(inline_summary),
            slack_channel_choices=broadcast_choice,
            awaiting_ready=payload.draft,
            slack_channel_id=channel_id,
            slack_thread_ts=thread_ts,
            run_config=origin.run_config,
            requested_by_user_id=requester.id if requester is not None else None,
        ).save()
        if approval.awaiting_ready:
            readiness_warning = await ReviewCard(approval).prompt_author_ready()
            if readiness_warning:
                await self.discard(approval)
                if displaced is not None:
                    await ReviewCard(displaced).reopen()
                return self.failure(readiness_warning)
            await ReviewCard(approval).remove_superseded()
            return {
                "success": True,
                "approval_id": str(approval.id),
                "pr_url": pr_ref.url,
                "head_sha": head_sha,
                "slack_channel_id": channel_id,
                "completion_reply_required": True,
                "next": "The full draft card was sent only to the author by DM. The thread card "
                "will be posted once they mark it ready. Keep a /baby-sit watch on the PR.",
            }
        try:
            message_ts = await ReviewCard(approval).post_expedited(title=payload.title, files=files)
        except SlackRequestError as exc:
            await self.discard(approval)
            if displaced is not None:
                await ReviewCard(displaced).reopen()
            return self.failure(f"Could not post the approval card in Slack: {exc.code}")
        except BaseException:
            await self.discard(approval)
            if displaced is not None:
                await ReviewCard(displaced).reopen()
            raise
        approval.slack_message_ts = message_ts
        card = ReviewCard(await approval.save())
        await card.link_on_github()
        await card.broadcast_configured()
        await card.remove_superseded()
        readiness_warning = await card.prompt_author_ready()
        next_step = self.next_step(
            reused=False,
            elsewhere=False,
            in_thread=bool(own_thread) and (channel_id, thread_ts) == (own_channel, own_thread),
            over_mcp=requester is not None,
        )
        if (
            requester is not None
            and requester.slack_user_id
            and await send_dm(
                requester.slack_user_id,
                f"Expedited review requested for <{pr_ref.url}|{pr_ref.repo}#{pr_ref.number}> "
                f"in <#{channel_id}>. Open SWE merges it once someone approves.",
            )
        ):
            next_step = f"{next_step} The person was sent a Slack DM pointing to the card."
        return {
            "success": True,
            "readiness_warning": readiness_warning,
            "approval_id": str(approval.id),
            "pr_url": pr_ref.url,
            "head_sha": head_sha,
            "changed_lines": verdict.changed_lines,
            "test_lines": verdict.test_lines,
            "generated_lines": verdict.generated_lines,
            "excluded_lines": verdict.excluded_lines,
            "slack_channel_id": channel_id,
            "next": readiness_warning or next_step,
        }
