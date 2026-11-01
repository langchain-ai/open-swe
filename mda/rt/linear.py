"""What Open SWE's Linear channel does with each Linear delivery, in this deployment.

Every delivery the channel handles goes to the backend's event log: the first call
for it carries it, so each event takes at most two calls (``record`` logs one that
asks nothing else). An agent session turn starts the coding agent's run on the
session's own thread. This side reads the turn and decides: whether its author may
run as themselves, which repository and workspace it works in, and why nothing
starts when nothing does, from what Open SWE knows about them
(``linear_session_facts``). It fetches the images the turn shows and writes the
run's input. The backend checks the choice again, indexes the thread and signs the
run's context (``plan_linear_session``). Issue events start Linear automations
(``rt.linear_automations``).
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, cast

from langchain_core.messages.content import create_text_block
from managed_deepagents import (
    ChannelRunOptions,
    LinearChannelThread,
    LinearEvent,
    LinearEventResult,
    LinearSessionCreatedEvent,
    LinearSessionPromptedEvent,
)
from managed_deepagents._channels.http.endpoint import EndpointDeclined  # noqa: PLC2701
from pydantic import JsonValue
from runtime_api.linear import (
    LINEAR_SESSION_FACTS,
    PLAN_LINEAR_SESSION,
    RECORD_LINEAR_DELIVERY,
    LinearDelivery,
    LinearIssue,
    LinearPerson,
    LinearSessionDeclined,
    LinearSessionFacts,
    LinearSessionFactsRequest,
    LinearSessionRun,
    LinearSessionTurn,
    PlanLinearSession,
    RepositoryChoice,
)

from rt.images import extract_image_urls, fetch_image_block
from rt.input_messages import RunInput, human_input, system_input, system_introduction
from rt.intake import ask_backend
from rt.prompts import prompt
from rt.repositories import is_allowed, repository_in

UNREADABLE_SESSION: Final = (
    "I couldn't read this Linear session, so I didn't start. Mention me on the issue "
    "again; if it keeps happening, let your Open SWE admin know."
)
NO_REPOSITORY: Final = (
    "Which repository should I work in? Mention it as `repo:owner/name` or link it on "
    "GitHub, or set a default repository in Open SWE settings."
)


def decline_text(declined: LinearSessionDeclined) -> str:
    """What the session hears when the backend starts nothing for a turn."""
    if declined.reason == "no_repository":
        return NO_REPOSITORY
    if declined.reason == "repository_not_allowed":
        return (
            f"I'm not allowed to work in `{declined.repository}`. Name a repository "
            "Open SWE may work in, or ask your Open SWE admin to allow this one."
        )
    # Without a dashboard URL the text names the settings page, since the session
    # must still be told why nothing started.
    settings = (
        f"[your Open SWE settings]({declined.settings_url})"
        if declined.settings_url
        else "Open SWE"
    )
    if declined.reason == "sign_in_again":
        return (
            "Your GitHub sign-in to Open SWE is no longer valid, so I can't work as you. "
            f"Sign in with GitHub again in {settings}, then mention me again."
        )
    return (
        "I don't know who you are in Open SWE yet, so I can't work as you. Sign in "
        f"with GitHub in {settings}, using the same email as your Linear account, "
        "then mention me again."
    )


def _declined(text: str) -> LinearEventResult:
    # MDA's Linear channel tells the session a handler's ``EndpointDeclined`` as it
    # does an endpoint's; its handler type doesn't name it.
    return cast(LinearEventResult, EndpointDeclined(text))


def _person(person: object) -> LinearPerson | None:
    """Who a payload names. Linear sends ``email`` too, which MDA's schema keeps."""
    if not isinstance(person, Mapping) or not person:
        return None
    fields = cast(Mapping[str, object], person)
    return LinearPerson(
        id=str(fields.get("id") or ""),
        name=str(fields.get("name") or ""),
        email=str(fields.get("email") or ""),
    )


def _sender_id(author: LinearPerson) -> str:
    """The author as the agent knows Linear people, as the backend names them."""
    key = author.id or author.email or author.name or "unknown"
    return f"linear:{key.replace(' ', '-')}"


@dataclass(frozen=True)
class SessionTurn:
    """One turn of a Linear agent session: who asked what about which issue."""

    turn: LinearSessionTurn

    @classmethod
    def of(
        cls, event: LinearSessionCreatedEvent | LinearSessionPromptedEvent, thread_id: str
    ) -> SessionTurn | None:
        """The turn an event carries; ``None`` for a session without its issue."""
        payload = event.payload
        session = payload["agentSession"]
        issue = session.get("issue")
        if not issue:
            return None
        creator = _person(session.get("creator"))
        actor = _person(payload.get("actor"))
        if isinstance(event, LinearSessionPromptedEvent):
            # A reply in the session.
            activity = event.payload["agentActivity"]
            replier = _person(activity.get("user"))
            replier_id = activity.get("userId")
            if replier is None and replier_id and (creator is None or creator.id != replier_id):
                # Someone other than the creator, named only by id: they sign in as
                # themselves, never borrowing the creator's identity.
                replier = LinearPerson(id=replier_id)
            author = replier or creator or actor
            comment_id = activity.get("sourceCommentId") or ""
            body = activity["content"].get("body") or ""
        else:
            # The mention or delegation that opened it.
            comment = event.payload["agentSession"].get("comment")
            author = creator or actor
            comment_id = comment["id"] if comment else ""
            body = (comment.get("body") if comment else None) or ""
        description = issue.get("description")
        return cls(
            LinearSessionTurn.model_validate(
                {
                    "thread_id": thread_id,
                    "session_id": session["id"],
                    "issue": LinearIssue(
                        id=issue["id"],
                        identifier=issue.get("identifier") or "",
                        title=issue.get("title") or "",
                        description=description if isinstance(description, str) else None,
                        url=issue.get("url") or "",
                    ),
                    "author": author or LinearPerson(),
                    "body": body,
                    "comment_id": comment_id,
                }
            )
        )

    def repository(self, facts: LinearSessionFacts) -> RepositoryChoice | LinearSessionDeclined:
        """The repository the turn works in, when one is named, remembered or preferred.

        A repository the turn names wins; then the one the session's thread already
        works in, so a follow-up needn't name it again; then one the issue names;
        then the author's and the instance's defaults. With it, the thread's
        workspace, else the one whose default chose it; ``None`` leaves it to the
        workspace that owns the repository. Otherwise why the turn starts nothing.
        """
        issue = self.turn.issue
        owner = facts.policy.default_owner
        named = (
            repository_in(self.turn.body, owner)
            or facts.thread_repository
            or repository_in(f"{issue.title}\n{issue.description or ''}", owner, explicit_only=True)
        )
        choice: RepositoryChoice | None = None
        if named:
            choice = RepositoryChoice(repository=named, workspace=facts.thread_workspace)
        elif facts.personal_default is not None:
            choice = RepositoryChoice(
                repository=facts.personal_default.repository,
                workspace=facts.thread_workspace or facts.personal_default.workspace,
            )
        elif facts.instance_default:
            choice = RepositoryChoice(
                repository=facts.instance_default, workspace=facts.thread_workspace
            )
        if choice is None:
            return LinearSessionDeclined(reason="no_repository")
        if not is_allowed(
            choice.repository, orgs=facts.policy.allowed_orgs, repos=facts.policy.allowed_repos
        ):
            return LinearSessionDeclined(
                reason="repository_not_allowed", repository=choice.repository
            )
        return choice

    async def run_input(self, run: LinearSessionRun) -> RunInput:
        """The issue, as the system tells it, then the author's words, each with its images."""
        issue, author = self.turn.issue, self.turn.author
        issue_prompt = prompt(
            "linear-issue",
            repository=run.repository,
            title=run.title,
            triggered_by=author.name or None,
            identifier=issue.identifier,
            issue_id=issue.id,
            ticket_url=issue.url,
            description=issue.description or "No description",
        )
        description_urls = extract_image_urls(issue.description or "")
        comment_urls = extract_image_urls(self.turn.body)
        images = await _images([*description_urls, *comment_urls])
        issue_blocks: list[dict[str, object]] = [
            cast(dict[str, object], create_text_block(issue_prompt)),
            *(images[url] for url in description_urls if url in images),
        ]
        comment_images = [images[url] for url in comment_urls if url in images]
        body: str | list[dict[str, object]] = (
            [cast(dict[str, object], create_text_block(self.turn.body)), *comment_images]
            if comment_images
            else self.turn.body
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
                                "id": issue.id,
                                "identifier": issue.identifier,
                                "url": issue.url,
                                "repository": run.repository,
                                "title": issue.title,
                            }
                        },
                    },
                ),
                human_input(
                    body,
                    {
                        "sender_id": _sender_id(author),
                        "surface": "linear",
                        "kind": "human",
                        "data": {
                            "comment_id": self.turn.comment_id,
                            "session_id": self.turn.session_id,
                        },
                    },
                ),
            ]
        }


async def _images(urls: list[str]) -> dict[str, dict[str, object]]:
    """Image blocks by URL, for the images that could be fetched."""
    urls = list(dict.fromkeys(urls))
    fetched = await asyncio.gather(*(fetch_image_block(url) for url in urls))
    return {
        url: cast(dict[str, object], block)
        for url, block in zip(urls, fetched, strict=True)
        if block
    }


def delivery_of(event: LinearEvent) -> LinearDelivery:
    """The delivery as the backend logs it."""
    return LinearDelivery(
        key=event.key,
        delivery_id=event.delivery_id,
        payload=cast(dict[str, JsonValue], event.payload),
    )


async def record(event: LinearEvent, _thread: LinearChannelThread) -> LinearEventResult:
    """Hand the delivery to the event log; it starts nothing here."""
    await ask_backend(
        RECORD_LINEAR_DELIVERY, delivery_of(event), msg_id=f"msg_{event.delivery_id}_record"
    )
    return None


async def start_session(
    event: LinearSessionCreatedEvent | LinearSessionPromptedEvent, thread: LinearChannelThread
) -> LinearEventResult:
    """The run a session turn starts on its thread, or why it starts none."""
    turn = SessionTurn.of(event, thread.id)
    if turn is None:
        await record(event, thread)
        return _declined(UNREADABLE_SESSION)
    facts = await ask_backend(
        LINEAR_SESSION_FACTS,
        LinearSessionFactsRequest(
            delivery=delivery_of(event),
            thread_id=turn.turn.thread_id,
            author_email=turn.turn.author.email,
        ),
        msg_id=f"msg_{event.delivery_id}_facts",
    )
    if facts.author != "signed_in":
        reason = "sign_in_again" if facts.author == "sign_in_again" else "sign_in"
        return _declined(
            decline_text(LinearSessionDeclined(reason=reason, settings_url=facts.settings_url))
        )
    choice = turn.repository(facts)
    if isinstance(choice, LinearSessionDeclined):
        return _declined(decline_text(choice))
    started = (
        await ask_backend(
            PLAN_LINEAR_SESSION,
            PlanLinearSession(
                turn=turn.turn, repository=choice.repository, workspace=choice.workspace
            ),
            msg_id=f"msg_{event.delivery_id}",
        )
    ).answer
    if isinstance(started, LinearSessionDeclined):
        return _declined(decline_text(started))
    options: ChannelRunOptions = {"context": started.context, "metadata": started.metadata}
    if started.webhook:
        options["webhook"] = started.webhook
    return (await turn.run_input(started), options)
