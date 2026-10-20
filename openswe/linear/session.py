"""A run for a Linear agent session, prepared for the MDA channel that starts it.

MDA's Linear channel receives the event and starts the run on the session's own
thread, so the conversation's reply, Stop and errors reach Linear. Each agent
session is its own thread, so a second session on the same issue is a new
conversation, with the issue's title. The backend decides everything else, as it
does for its own runs: the repository, the workspace, the input, and the signed
run token the run calls back with. The run carries the backend's run-complete
webhook, which completes it as it does the backend's own runs; the channel reports
its failures in the session.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

import httpx2
from langchain_core.messages.content import create_text_block
from managed_deepagents import (
    EndpointAnswer,
    LinearSessionCreatedPayload,
    LinearSessionPromptedPayload,
)
from managed_deepagents import JsonValue as AnswerJson
from pydantic import JsonValue, TypeAdapter, ValidationError

from openswe.dispatch import CHANNEL_METADATA_KEY, COMPLETION_WEBHOOK_URL, prepare_durable_run
from openswe.input_messages import (
    PersonIdentity,
    RunInput,
    human_input,
    system_input,
    system_introduction,
)
from openswe.prompts import prompt
from openswe.source_context import SourceContext
from openswe.threads.router import adopt_channel_run
from openswe.threads.runtimes import RUNTIMES
from openswe.users import User
from openswe.utils.http_pool import pooled_transport
from openswe.webhooks import common
from openswe.workspaces.routing import resolve_workspace

#: The channel's name in run and thread metadata (``CHANNEL_METADATA_KEY``).
LINEAR_CHANNEL = "linear"
SESSION_EVENT_KEYS: frozenset[str] = frozenset(
    {"AgentSessionEvent.created", "AgentSessionEvent.prompted"}
)


_PAYLOADS: dict[str, TypeAdapter[Any]] = {
    "AgentSessionEvent.created": TypeAdapter(LinearSessionCreatedPayload),
    "AgentSessionEvent.prompted": TypeAdapter(LinearSessionPromptedPayload),
}


@dataclass(frozen=True)
class LinearAuthor:
    """Who wrote a session turn. Linear sends ``email`` too, which MDA's schema keeps."""

    id: str = ""
    name: str = ""
    email: str = ""

    @classmethod
    def of(cls, person: Mapping[str, object] | None) -> LinearAuthor | None:
        if not person:
            return None
        return cls(
            id=str(person.get("id") or ""),
            name=str(person.get("name") or ""),
            email=str(person.get("email") or ""),
        )

    def identity(self) -> PersonIdentity:
        key = self.id or self.email or self.name or "unknown"
        person: PersonIdentity = {"id": f"linear:{key.replace(' ', '-')}"}
        if self.name:
            person["display_name"] = self.name
        if self.email:
            person["email"] = self.email
        return person


@dataclass(frozen=True)
class _Issue:
    id: str
    identifier: str = ""
    title: str = ""
    description: str | None = None
    url: str = ""

    @classmethod
    def of(cls, issue: Mapping[str, Any]) -> _Issue:
        return cls(
            id=str(issue["id"]),
            identifier=issue.get("identifier") or "",
            title=issue.get("title") or "",
            description=issue.get("description"),
            url=issue.get("url") or "",
        )


class LinearSessionTurn:
    """One turn of a Linear agent session: who asked what about which issue."""

    def __init__(
        self,
        payload: LinearSessionCreatedPayload | LinearSessionPromptedPayload,
        issue: Mapping[str, Any],
    ) -> None:
        session = payload["agentSession"]
        self.session_id = session["id"]
        self.issue = _Issue.of(issue)
        creator = LinearAuthor.of(cast(Mapping[str, object] | None, session.get("creator")))
        actor = LinearAuthor.of(cast(Mapping[str, object] | None, payload.get("actor")))
        if payload["action"] == "prompted":
            # A reply in the session.
            activity = cast(LinearSessionPromptedPayload, payload)["agentActivity"]
            user = LinearAuthor.of(cast(Mapping[str, object] | None, activity.get("user")))
            self.comment_id = activity.get("sourceCommentId") or ""
            self.body = activity["content"].get("body") or ""
            self.author = user or creator or actor or LinearAuthor()
        else:
            # The mention or delegation that opened it.
            comment = session.get("comment")
            self.comment_id = comment["id"] if comment else ""
            self.body = (comment.get("body") if comment else None) or ""
            self.author = creator or actor or LinearAuthor()

    @classmethod
    def parse(cls, key: str, payload: object) -> LinearSessionTurn | None:
        """The session turn an event carries, or None for any other Linear event.

        The payload is checked against MDA's schema for the event; a session
        without its issue has nothing to work on.
        """
        adapter = _PAYLOADS.get(key)
        if adapter is None:
            return None
        try:
            validated = adapter.validate_python(payload)
        except ValidationError:
            return None
        issue = validated["agentSession"].get("issue")
        return cls(validated, issue) if issue else None

    async def repository(self, thread_id: str) -> tuple[dict[str, str], str | None] | None:
        """The repository the turn works in, when one is named, preferred or allowed.

        With it, the workspace whose personal default chose it, if one did.
        """
        repo = common.extract_repo_from_text(self.body, default_owner=common.DEFAULT_REPO_OWNER)
        workspace: str | None = None
        if not repo and self.author.email:
            try:
                repo, workspace = await self._personal_default(self.author.email, thread_id)
            except Exception:
                common.logger.exception(
                    "Reading a Linear user's default repository failed",
                    extra={"linear_issue": self.issue.id},
                )
        if not repo:
            repo = (await common.get_workspace_settings()).default_repo
        if not repo:
            return None
        if not common.is_repo_allowed(repo):
            common.logger.warning(
                "Linear session names a repository outside the allowlist",
                extra={"linear_issue": self.issue.id},
            )
            return None
        return repo, workspace

    @staticmethod
    async def _personal_default(
        email: str, thread_id: str
    ) -> tuple[dict[str, str] | None, str | None]:
        """The author's default for the thread's workspace, else their global one."""
        user = await User.for_email(email)
        if user is None:
            return None, None
        workspace = await common.get_thread_workspace(thread_id)
        if workspace is None:
            workspace = (await resolve_workspace(login=user.github_login)).slug
        personal = await user.default_repo(workspace, fallback_global=False)
        if personal:
            return personal, workspace
        return await user.default_repo(), None

    async def prepare_run(self, thread_id: str) -> EndpointAnswer | None:
        """Index the session's thread and prepare the run for it, or None to start nothing."""
        chosen = await self.repository(thread_id)
        if chosen is None:
            return None
        repo, default_workspace = chosen
        repository = f"{repo['owner']}/{repo['name']}"
        title = self.issue.title or self.issue.identifier or "Linear issue"
        description = self.issue.description or "No description"
        mapped_login = await User.login_for_email(self.author.email) if self.author.email else None
        workspace = (
            await common.get_thread_workspace(thread_id)
            or default_workspace
            or await common.workspace_for_repo_config(repo)
        )

        issue_prompt = prompt(
            "runs/linear-issue",
            repository=repository,
            title=title,
            triggered_by=self.author.name or None,
            identifier=self.issue.identifier,
            issue_id=self.issue.id,
            ticket_url=self.issue.url,
            description=description,
        )
        description_urls = common.extract_image_urls(description)
        comment_urls = common.extract_image_urls(self.body)
        images, model_override = await self._images(
            [*description_urls, *comment_urls], mapped_login, workspace
        )

        linear_issue: dict[str, JsonValue] = {
            "id": self.issue.id,
            "title": title,
            "url": self.issue.url,
            "identifier": self.issue.identifier,
            "triggering_user_name": self.author.name,
        }
        configurable: dict[str, JsonValue] = {
            "repo": cast(dict[str, JsonValue], repo),
            "linear_issue": linear_issue,
            "user_email": self.author.email or None,
            "source": "linear",
            "workspace": workspace,
            "environment": workspace,
        }
        if mapped_login:
            configurable["github_login"] = mapped_login
        if model_override:
            configurable["agent_model_id"], configurable["agent_effort"] = model_override

        # First, so the metadata write below doesn't create the thread on MDA.
        await adopt_channel_run(thread_id, LINEAR_CHANNEL)
        await common.upsert_agent_thread_metadata(
            thread_id,
            source="linear",
            repo_config=repo,
            github_login=mapped_login or "",
            user_email=self.author.email,
            title=title,
            source_context=SourceContext.parse({"linear_issue": linear_issue}),
            workspace=workspace,
        )

        run_config = await prepare_durable_run(
            "agent",
            {"configurable": configurable},
            {**common.AGENT_VERSION_METADATA, CHANNEL_METADATA_KEY: LINEAR_CHANNEL},
            source="linear",
        )
        rewritten = await RUNTIMES.mda_runtime.run_payload(
            thread_id, {"assistant_id": "agent", "config": cast(JsonValue, run_config)}
        )
        prepared: EndpointAnswer = {
            "input": cast(
                JsonValue, self._input(issue_prompt, repository, images, description_urls)
            ),
            "context": cast(dict[str, AnswerJson], rewritten["context"]),
            "metadata": cast(dict[str, AnswerJson], run_config["metadata"]),
        }
        if COMPLETION_WEBHOOK_URL:
            prepared["webhook"] = COMPLETION_WEBHOOK_URL
        return prepared

    async def _images(
        self, urls: list[str], login: str | None, workspace: str | None
    ) -> tuple[dict[str, dict[str, JsonValue]], tuple[str, str] | None]:
        """Image blocks by URL, and the vision model to use when the configured one can't see."""
        if not urls:
            return {}, None
        urls = common.dedupe_urls(urls)
        override: tuple[str, str] | None = None
        model_id = await common.resolve_agent_model_id(login, workspace=workspace)
        if not common.model_supports_images(model_id):
            override = common.default_vision_model_pair()
        blocks: dict[str, dict[str, JsonValue]] = {}
        async with httpx2.AsyncClient(
            transport=pooled_transport(), timeout=common.DEFAULT_HTTP_TIMEOUT
        ) as client:
            for url in urls:
                block = await common.fetch_image_block(url, client)
                if block:
                    blocks[url] = cast(dict[str, JsonValue], block)
        return blocks, override

    def _input(
        self,
        issue_prompt: str,
        repository: str,
        images: dict[str, dict[str, JsonValue]],
        description_urls: list[str],
    ) -> RunInput:
        issue_blocks: list[dict[str, JsonValue]] = [
            cast(dict[str, JsonValue], create_text_block(issue_prompt)),
            *(images[url] for url in common.dedupe_urls(description_urls) if url in images),
        ]
        comment_images = [
            images[url]
            for url in common.dedupe_urls(common.extract_image_urls(self.body))
            if url in images
        ]
        body: str | list[dict[str, JsonValue]] = (
            [cast(dict[str, JsonValue], create_text_block(self.body)), *comment_images]
            if comment_images
            else self.body
        )
        return {
            "messages": [
                system_introduction(
                    {
                        "id": "system:linear-issue",
                        "display_name": "Linear issue",
                        "platform": "linear",
                    }
                ),
                system_input(
                    issue_blocks if len(issue_blocks) > 1 else issue_prompt,
                    {
                        "sender_id": "system:linear-issue",
                        "surface": "linear",
                        "kind": "system",
                        "data": {
                            "issue": {
                                "id": self.issue.id,
                                "identifier": self.issue.identifier,
                                "url": self.issue.url,
                                "repository": repository,
                                "title": self.issue.title,
                            }
                        },
                    },
                ),
                human_input(
                    body,
                    {
                        "sender_id": self.author.identity()["id"],
                        "surface": "linear",
                        "kind": "human",
                        "data": {"comment_id": self.comment_id, "session_id": self.session_id},
                    },
                ),
            ]
        }
