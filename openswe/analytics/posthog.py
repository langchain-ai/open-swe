"""Optional PostHog capture without tool content or page identifiers."""

import logging
from typing import Literal, Self

import httpx
from langgraph.config import get_config
from pydantic import BaseModel, ValidationError

from openswe.config import ENV
from openswe.github.pull_requests import PullRequest
from openswe.run_config import RunConfig
from openswe.users import User
from openswe.webhooks.event_log import LoggedEvent

logger = logging.getLogger(__name__)


async def _capture(
    event: str, properties: dict[str, object], *, timestamp: str | None = None
) -> None:
    key = ENV.POSTHOG_API_KEY.get()
    if not key:
        return
    payload: dict[str, object] = {
        "api_key": key,
        "event": event,
        "properties": {
            **{
                key: value
                for key, value in properties.items()
                if key
                in {
                    "distinct_id",
                    "$process_person_profile",
                    "tool",
                    "is_error",
                    "surface",
                    "source",
                    "event_type",
                    "action",
                    "workspace_id",
                    "repository_id",
                    "repo",
                    "pull_request_id",
                    "pull_request_author_id",
                    "sender_type",
                    "visibility",
                }
            },
            "product": "open-swe",
            "environment": ENV.DD_ENV.get(),
            "$ip": "0.0.0.0",
            "$geoip_disable": True,
        },
    }
    traits = properties.get("$set")
    if isinstance(traits, dict):
        capture_properties = payload["properties"]
        if isinstance(capture_properties, dict):
            capture_properties["$set"] = {
                key: value
                for key, value in traits.items()
                if (key in {"email", "github_login"} and isinstance(value, str) and value)
                or (key == "is_bot" and isinstance(value, bool))
            }
    if timestamp:
        payload["timestamp"] = timestamp
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.post(
                f"{ENV.POSTHOG_HOST.get().rstrip('/')}/i/v0/e/", json=payload
            )
            response.raise_for_status()
    except Exception:
        logger.warning("PostHog usage delivery failed", exc_info=True)


async def record_usage(
    *,
    login: str,
    email: str | None,
    name: str,
    properties: dict[str, str | bool],
) -> None:
    if not ENV.POSTHOG_API_KEY.get() or not login:
        return
    try:
        user = await User.for_login("github", login)
        if user is None:
            logger.warning("PostHog usage identity could not be resolved")
            return
        await _capture(
            name,
            {
                **properties,
                "distinct_id": str(user.id),
                "$set": {"email": email, "github_login": login},
            },
        )
    except Exception:
        logger.warning("PostHog usage capture failed", exc_info=True)


class _GitHubSender(BaseModel):
    id: int
    login: str
    type: str = ""


class _SlackMessage(BaseModel):
    user: str = ""
    bot_id: str = ""
    message: _SlackMessage | None = None


class _SlackDelivery(BaseModel):
    event: _SlackMessage


class WebhookSender(BaseModel):
    """Who a logged event counts as in PostHog: a person, a bot, or a provider account."""

    distinct_id: str
    type: Literal["user", "bot", "system"]
    is_person: bool
    github_login: str = ""

    @classmethod
    def of(cls, event: LoggedEvent) -> Self:
        person = event.user_id or (
            event.visible_to_user_id if event.visibility == "private" else None
        )
        if person:
            return cls(distinct_id=str(person), type="user", is_person=True)
        try:
            if event.source == "github" and isinstance(event.payload, dict):
                sender = _GitHubSender.model_validate(event.payload.get("sender"))
                if sender.type == "Bot":
                    return cls(
                        distinct_id=f"github:{sender.id}",
                        type="bot",
                        is_person=True,
                        github_login=sender.login,
                    )
                return cls(distinct_id=f"github:{sender.id}", type="user", is_person=False)
            if event.source == "slack":
                slack = _SlackDelivery.model_validate(event.payload).event
                message = slack.message or slack
                if message.user:
                    return cls(distinct_id=f"slack:{message.user}", type="user", is_person=False)
                if message.bot_id:
                    return cls(distinct_id=f"slack:{message.bot_id}", type="bot", is_person=True)
        except ValidationError:
            logger.debug("Logged event names no sender", extra={"webhook_source": event.source})
        return cls(distinct_id=f"open-swe:webhook:{event.source}", type="system", is_person=False)

    @property
    def traits(self) -> dict[str, object]:
        if self.type != "bot":
            return {}
        return {
            "is_bot": True,
            **({"github_login": self.github_login} if self.github_login else {}),
        }


async def record_webhook(event: LoggedEvent) -> None:
    if not ENV.POSTHOG_API_KEY.get():
        return
    # A private event (a person's own thread, a DM) records only who acted and when.
    private = event.visibility == "private"
    try:
        sender = WebhookSender.of(event)
        author_id = (
            await PullRequest.author_user_id_of(event.pull_request_id)
            if event.pull_request_id and not private
            else None
        )
    except Exception:
        logger.warning("PostHog webhook attribution failed", exc_info=True)
        return
    action = event.payload.get("action") if isinstance(event.payload, dict) else None
    repository = event.payload.get("repository") if isinstance(event.payload, dict) else None
    repo = repository.get("full_name") if isinstance(repository, dict) else None
    await _capture(
        "Webhook Received",
        {
            "distinct_id": sender.distinct_id,
            "$process_person_profile": sender.is_person,
            "$set": sender.traits,
            "sender_type": sender.type,
            "source": event.source,
            "event_type": event.base_event_type,
            "action": action if isinstance(action, str) else "",
            "surface": "webhook",
            "visibility": event.visibility,
            "workspace_id": str(event.workspace_id) if event.workspace_id else None,
            **(
                {}
                if private
                else {
                    **(
                        {"repo": repo} if event.source == "github" and isinstance(repo, str) else {}
                    ),
                    **{
                        field: str(value) if value else None
                        for field, value in {
                            "repository_id": event.repository_id,
                            "pull_request_id": event.pull_request_id,
                            "pull_request_author_id": author_id,
                        }.items()
                    },
                }
            ),
        },
        timestamp=event.received_at.isoformat(),
    )


async def record_mcp_tool(tool: str, is_error: bool) -> None:
    if not ENV.POSTHOG_API_KEY.get():
        return
    try:
        cfg = RunConfig.from_config(get_config())
        await record_usage(
            login=cfg.github_login or "",
            email=cfg.user_email,
            name="MCP Tool Called",
            properties={
                "tool": tool,
                "surface": "mcp",
                "is_error": is_error,
                "repo": cfg.repo_full_name,
            },
        )
    except Exception:
        logger.warning("PostHog MCP capture failed", exc_info=True)
