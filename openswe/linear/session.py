"""A run for a Linear agent session, prepared for the MDA channel that starts it.

MDA's Linear channel receives the event and starts the run on the session's own
thread, so the conversation's reply, Stop and errors reach Linear. Each agent
session is its own thread, so a second session on the same issue is a new
conversation, with the issue's title. The backend decides everything else, as it
does for its own runs: the repository, the workspace, the input, and the signed
run token the run calls back with. The run's ``after_agent`` hook completes it
(``openswe.mda_channels.completion``); the channel reports its failures.
"""

from typing import TypedDict, cast

import httpx2
from langchain_core.messages.content import create_text_block
from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic.alias_generators import to_camel

from openswe.dispatch import CHANNEL_METADATA_KEY, prepare_durable_run
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

#: The channel's name in run and thread metadata (``CHANNEL_METADATA_KEY``).
LINEAR_CHANNEL = "linear"
SESSION_EVENT_KEYS: frozenset[str] = frozenset(
    {"AgentSessionEvent.created", "AgentSessionEvent.prompted"}
)


class _Model(BaseModel):
    """Linear's camelCase payload, keeping only what a turn needs."""

    model_config = ConfigDict(extra="ignore", alias_generator=to_camel, populate_by_name=True)


class LinearPerson(_Model):
    id: str = ""
    name: str = ""
    email: str = ""

    def identity(self) -> PersonIdentity:
        key = self.id or self.email or self.name or "unknown"
        person: PersonIdentity = {"id": f"linear:{key.replace(' ', '-')}"}
        if self.name:
            person["display_name"] = self.name
        if self.email:
            person["email"] = self.email
        return person


class _Issue(_Model):
    id: str
    identifier: str = ""
    title: str = ""
    description: str | None = None
    url: str = ""


class _Comment(_Model):
    id: str
    body: str = ""


class _Session(_Model):
    id: str
    issue: _Issue
    comment: _Comment | None = None
    creator: LinearPerson | None = None


class _ActivityContent(_Model):
    body: str = ""


class _Activity(_Model):
    content: _ActivityContent
    source_comment_id: str | None = None
    user: LinearPerson | None = None


class _Payload(_Model):
    type: str
    action: str
    agent_session: _Session
    agent_activity: _Activity | None = None
    actor: LinearPerson | None = None


class PreparedRun(TypedDict):
    """What the channel starts the run with."""

    input: RunInput
    context: dict[str, JsonValue]
    metadata: dict[str, JsonValue]


class LinearSessionTurn:
    """One turn of a Linear agent session: who asked what about which issue."""

    def __init__(self, payload: _Payload) -> None:
        session = payload.agent_session
        self.session_id = session.id
        self.issue = session.issue
        activity = payload.agent_activity
        if activity is not None:
            # A reply in the session.
            self.comment_id = activity.source_comment_id or ""
            self.body = activity.content.body
            self.author = activity.user or session.creator or payload.actor or LinearPerson()
        else:
            # The mention or delegation that opened it.
            self.comment_id = session.comment.id if session.comment else ""
            self.body = session.comment.body if session.comment else ""
            self.author = session.creator or payload.actor or LinearPerson()

    @classmethod
    def parse(cls, key: str, payload: object) -> LinearSessionTurn | None:
        """The session turn an event carries, or None for any other Linear event."""
        if key not in SESSION_EVENT_KEYS or not isinstance(payload, dict):
            return None
        return cls(_Payload.model_validate(payload))

    async def repository(self) -> dict[str, str] | None:
        """The repository the turn works in, when one is named, preferred or allowed."""
        repo = common.extract_repo_from_text(self.body, default_owner=common.DEFAULT_REPO_OWNER)
        if not repo and self.author.email:
            try:
                repo = await common.get_profile_default_repo(
                    await User.login_for_email(self.author.email)
                )
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
        return repo

    async def prepare_run(self, thread_id: str) -> PreparedRun | None:
        """Index the session's thread and prepare the run for it, or None to start nothing."""
        repo = await self.repository()
        if repo is None:
            return None
        repository = f"{repo['owner']}/{repo['name']}"
        title = self.issue.title or self.issue.identifier or "Linear issue"
        description = self.issue.description or "No description"
        mapped_login = await User.login_for_email(self.author.email) if self.author.email else None
        workspace = await common.get_thread_workspace(
            thread_id
        ) or await common.workspace_for_repo_config(repo)

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
        await adopt_channel_run(thread_id, LINEAR_CHANNEL)

        run_config = await prepare_durable_run(
            "agent",
            {"configurable": configurable},
            {**common.AGENT_VERSION_METADATA, CHANNEL_METADATA_KEY: LINEAR_CHANNEL},
            source="linear",
        )
        rewritten = await RUNTIMES.mda_runtime.run_payload(
            thread_id, {"assistant_id": "agent", "config": cast(JsonValue, run_config)}
        )
        return {
            "input": self._input(issue_prompt, repository, images, description_urls),
            "context": cast(dict[str, JsonValue], rewritten["context"]),
            "metadata": cast(dict[str, JsonValue], run_config["metadata"]),
        }

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
