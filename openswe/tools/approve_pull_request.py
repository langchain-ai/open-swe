"""Submit an explicitly requested approval as the authenticated requester."""

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import text

from openswe.audit_logs.tools import audit_tool
from openswe.credential_scope import pr_author_login
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.database import postgres
from openswe.github.http import GitHubClient, GitHubError, GitHubSignInRequired
from openswe.run_config import RunConfig
from openswe.slack.client import parse_github_pr_url
from openswe.tools.access import direct_user_run
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_thread_url


@dataclass(frozen=True)
class ApprovalAuthorization:
    login: str
    message_id: str
    quote: str
    thread_id: str

    @classmethod
    async def resolve(cls, cfg: RunConfig, quote: str) -> ApprovalAuthorization:
        if (
            not direct_user_run(cfg)
            or not cfg.github_login
            or not cfg.thread_id
            or not cfg.run_id
            or not quote.strip()
            or len(quote) > 8000
        ):
            raise ValueError("Approval requires a direct human request and its exact message text")
        login = await pr_author_login()
        if not login:
            raise ValueError("System threads cannot approve pull requests as a person")
        actor = await User.for_login("github", cfg.github_login)
        if actor is None or actor.github_login.lower() != login.lower():
            raise ValueError("Approval must use the current requester's linked GitHub account")
        async with postgres.read_only_transaction() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT message.message_id, message.sender, message.text
                    FROM thread_message AS message
                    JOIN thread_turn AS turn
                      ON turn.thread_id = message.thread_id
                     AND turn.turn_id = message.turn_id
                    WHERE message.thread_id = :thread_id
                      AND turn.run_id = :run_id
                      AND message.role = 'human'
                      AND message.namespace = ARRAY[]::text[]
                    ORDER BY message.created_at DESC, message.message_id DESC
                    LIMIT 1
                    """
                ),
                {"thread_id": cfg.thread_id, "run_id": cfg.run_id},
            )
            row = result.mappings().one_or_none()
        if row is not None and row["text"] == quote:
            sender = row["sender"]
            sender_login = sender.get("login") if isinstance(sender, dict) else None
            if isinstance(sender_login, str):
                person = await User.for_login("github", sender_login.removeprefix("github:"))
                if person is not None and person.id == actor.id:
                    return cls(login, row["message_id"], quote, cfg.thread_id)
        raise ValueError("Evidence must be the requester's exact human message in this run")

    def review_body(self) -> str:
        source = dashboard_thread_url(self.thread_id)
        citation = f"[request]({source})" if source else f"thread `{self.thread_id}`"
        quoted = "\n".join(f"> {line}" for line in self.quote.splitlines())
        return (
            f"Approved by Open SWE on behalf of @{self.login}, at their explicit {citation}.\n\n"
            f"{quoted}\n\nAuthorization message: `{self.message_id}`."
        )


@audit_tool()
async def approve_pull_request(
    pr_url: str, head_sha: str, authorization_quote: str
) -> dict[str, object]:
    """Approve as the requester, citing their recorded instruction."""
    ref = parse_github_pr_url(pr_url)
    if ref is None:
        return {"success": False, "error": "pr_url must be a GitHub pull request URL"}
    try:
        authorization = await ApprovalAuthorization.resolve(
            RunConfig.from_runtime(), authorization_quote
        )
        await require_repo_access_for_user(authorization.login, f"{ref.owner}/{ref.repo}")
        async with GitHubClient.as_user(authorization.login) as github:
            pull = github.repo(ref.owner, ref.repo).pull_request(ref.number)
            record = await pull.pull()
            author = record.get("user")
            if (
                isinstance(author, dict)
                and str(author.get("login", "")).lower() == authorization.login.lower()
            ):
                raise ValueError("You cannot approve your own pull request")
            if record.get("state") != "open" or record.get("draft") is not False:
                raise ValueError("Only open, non-draft pull requests can be approved")
            head = record.get("head")
            if not head_sha or not isinstance(head, dict) or head.get("sha") != head_sha:
                raise ValueError(
                    "The pull request head changed; inspect it before requesting approval again"
                )
            review = await pull.create_review(
                {"event": "APPROVE", "commit_id": head_sha, "body": authorization.review_body()}
            )
        if not isinstance(review, dict) or review.get("state") != "APPROVED":
            return {"success": False, "error": "GitHub did not confirm an approved review"}
        return {
            "success": True,
            "pr_url": f"https://github.com/{ref.owner}/{ref.repo}/pull/{ref.number}",
            "review_url": review.get("html_url"),
            "login": authorization.login,
            "head_sha": head_sha,
            "authorization_message_id": authorization.message_id,
        }
    except (ValueError, RuntimeError, PermissionError) as exc:
        return {"success": False, "error": str(exc)}
    except HTTPException as exc:
        return {"success": False, "error": str(exc.detail)}
    except GitHubSignInRequired as exc:
        return {"success": False, "error": str(exc)}
    except GitHubError as exc:
        return {"success": False, "error": exc.message}
