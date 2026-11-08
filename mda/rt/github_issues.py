"""An ``@openswe`` mention on a GitHub issue: the run it starts, written in this deployment.

The issue's conversation is the channel's thread. The first mention starts it with
the issue itself: the ``github-issue`` prompt, then the description and every
comment, each as its author wrote it; a later mention or an edit of the issue is a
follow-up, written as the person's own message. Anyone who isn't an Open SWE user
is fenced as untrusted (``rt.untrusted``), so the backend says who is
(``github_issue_facts``). The comments are read here, with the App's token
(``mda/github-token``), as the backend read them before. It writes byte for byte
what the backend's ``prepare_github_issue_run`` wrote:
``tests/github_prompt_backend.json`` holds what it wrote for each case.
"""

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, cast

import httpx2
from managed_deepagents import connections
from runtime_api.github import LOGIN_PATTERN, REPOSITORY_PATTERN

from rt.input_messages import (
    FieldValues,
    PersonIdentity,
    RunInput,
    RunMessage,
    SystemIdentity,
    human_input,
    person_introduction,
    system_input,
    system_introduction,
)
from rt.prompts import prompt
from rt.untrusted import fence, sanitize

logger = logging.getLogger(__name__)

# The App's installation token, which ``mda deploy`` stores for the GitHub channel.
GITHUB_TOKEN: Final = "mda/github-token"  # noqa: S105 - a connection name, not a secret
_API: Final = "https://api.github.com"
_HEADERS: Final = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
_TIMEOUT: Final = httpx2.Timeout(30.0)
# As the backend capped it: an issue with thousands of comments is read in part.
_MAX_PAGES: Final = 50
_PER_PAGE: Final = 100
_REPOSITORY: Final = re.compile(REPOSITORY_PATTERN)
_LOGIN: Final = re.compile(LOGIN_PATTERN)
# Open SWE's own replies, which the issue's prompt leaves out.
_BOT_MESSAGE_PREFIXES: Final = (
    "🔐 **GitHub Authentication Required**",
    "✅ **Pull Request Created**",
    "✅ **Pull Request Updated**",
    "**Pull Request Created**",
    "**Pull Request Updated**",
    "🤖 **Agent Response**",
    "❌ **Agent Error**",
)
_WEBHOOK: Final[SystemIdentity] = {
    "id": "system:github-webhook",
    "display_name": "GitHub webhook",
    "platform": "github",
}

type Comment = dict[str, object]


def is_login(login: str) -> bool:
    """Whether ``login`` is spelled as GitHub spells one, so the backend can look it up."""
    return _LOGIN.fullmatch(login) is not None


def _fields(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _fenced(author: str, body: str, trusted: frozenset[str]) -> str:
    return fence(body, registered=author.strip().lower() in trusted)


def _comments_text(comments: list[Comment], trusted: frozenset[str]) -> str:
    lines: list[str] = []
    for comment in comments:
        body = str(comment.get("body", ""))
        if not body or any(body.startswith(prefix) for prefix in _BOT_MESSAGE_PREFIXES):
            continue
        author = str(comment.get("author", "unknown"))
        lines.append(f"\n**{author}:**\n{_fenced(author, body, trusted)}\n")
    if not lines:
        return ""
    return "\n\n## Comments:\n" + "".join(lines)


def _person(login: str, user_id: object = None) -> PersonIdentity:
    stable = str(user_id) if user_id not in (None, "") else login or "unknown"
    person: PersonIdentity = {"id": f"github:{stable}"}
    if login:
        person["display_name"] = login
        person["github_login"] = login
    return person


@dataclass(frozen=True)
class IssueMention:
    """An issue that mentions Open SWE, or a comment on one that does, as GitHub sent it."""

    event: str
    owner: str
    name: str
    issue_id: str
    number: int
    title: str
    description: str
    url: str
    # Who opened the issue.
    author: str
    # Who the delivery is from, and their GitHub user ID.
    sender: str
    sender_id: object
    # The comment, for an ``issue_comment``.
    comment: Mapping[str, object]

    @classmethod
    def of(cls, event: str, payload: Mapping[str, object]) -> IssueMention | None:
        """The mention a delivery carries; ``None`` without its issue or repository."""
        issue = _fields(payload.get("issue"))
        repository = _fields(payload.get("repository"))
        sender = _fields(payload.get("sender"))
        owner = str(_fields(repository.get("owner")).get("login") or "")
        name = str(repository.get("name") or "")
        number = issue.get("number")
        issue_id = str(issue.get("id", ""))
        if (
            not issue_id
            or not isinstance(number, int)
            or not number
            or not _REPOSITORY.fullmatch(f"{owner}/{name}")
        ):
            logger.warning("GitHub issue mention names no issue or repository; skipping")
            return None
        return cls(
            event=event,
            owner=owner,
            name=name,
            issue_id=issue_id,
            number=number,
            title=str(issue.get("title", "No title") or ""),
            description=str(issue.get("body") or "No description"),
            url=str(issue.get("html_url", "") or issue.get("url", "") or ""),
            author=str(_fields(issue.get("user")).get("login", "") or ""),
            sender=str(sender.get("login", "") or ""),
            sender_id=sender.get("id"),
            comment=_fields(payload.get("comment")),
        )

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def comment_id(self) -> object:
        return self.comment.get("id")

    @property
    def comment_login(self) -> str:
        """Who wrote the comment, else the sender."""
        return str(_fields(self.comment.get("user")).get("login") or self.sender)

    def logins(self, comments: Sequence[Comment] = ()) -> list[str]:
        """The people the run's prompt quotes, the sender first, as the backend looks them up."""
        named = [self.sender, self.author, self.comment_login]
        named += [str(comment.get("author") or "") for comment in comments]
        return [login for login in dict.fromkeys(named) if is_login(login)]

    def follow_up(self, trusted: frozenset[str]) -> RunInput:
        """A mention on a conversation that already took the issue in: the person's words."""
        if self.event == "issue_comment":
            login = self.comment_login
            body = _fenced(login, str(self.comment.get("body", "")), trusted)
            text = f"**{login}:**\n{body}"
            sender = login
        else:
            text = (
                f"**{self.sender}:** updated the GitHub issue title/body.\n\n"
                f"Title: {sanitize(self.title)}\n\n"
                f"Description:\n{_fenced(self.sender, self.description, trusted)}"
            )
            sender = self.sender
        person = _person(sender, self.sender_id)
        comment_id = self.comment_id
        return {
            "messages": [
                human_input(
                    text,
                    {
                        "sender_id": person["id"],
                        "surface": "github",
                        "kind": "human",
                        "data": {
                            "issue": {"id": self.issue_id, "number": self.number, "url": self.url},
                            "event_type": self.event,
                            "comment_id": comment_id
                            if isinstance(comment_id, int | str) and comment_id
                            else "",
                        },
                    },
                )
            ]
        }

    def with_comment(self, comments: list[Comment]) -> list[Comment]:
        """The issue's comments, with the delivery's own in its place when GitHub hasn't
        listed it yet."""
        comment_id = self.comment_id
        if not comment_id or any(item.get("comment_id") == comment_id for item in comments):
            return comments
        added: Comment = {
            "body": self.comment.get("body", ""),
            "author": _fields(self.comment.get("user")).get("login", "unknown"),
            "created_at": self.comment.get("created_at", ""),
            "comment_id": comment_id,
        }
        return sorted([*comments, added], key=lambda item: str(item.get("created_at", "")))

    def first(self, comments: list[Comment], trusted: frozenset[str]) -> RunInput:
        """The conversation's first run: the issue, as the system tells it, then each
        person's words in order, introducing each the run doesn't already describe."""
        issue_prompt = prompt(
            "github-issue",
            repository=self.repository,
            triggered_by_line=f"## Triggered by: {self.sender}\n\n" if self.sender else "",
            issue_number=self.number,
            issue_id=self.issue_id,
            issue_url_line=f"## Issue URL: {self.url}\n\n" if self.url else "",
            title=sanitize(self.title),
            body=_fenced(self.author or self.sender, self.description, trusted),
            comments=_comments_text(comments, trusted),
        )
        issue_data: FieldValues = {
            "id": self.issue_id,
            "number": self.number,
            "url": self.url,
            "repository": self.repository,
            "title": self.title,
        }
        messages: list[RunMessage] = [
            system_introduction(_WEBHOOK),
            system_input(
                issue_prompt,
                {
                    "sender_id": _WEBHOOK["id"],
                    "surface": "github",
                    "kind": "system",
                    "data": {"issue": issue_data},
                },
            ),
        ]
        # The run describes the sender itself; only the others need an introduction.
        introduced = {_person(self.sender, self.sender_id)["id"]}
        quoted: list[Comment] = [
            {
                "author": self.author or self.sender,
                "body": self.description,
                "type": "description",
            },
            *comments,
        ]
        for comment in quoted:
            author = str(comment.get("author") or "unknown")
            person = _person(
                author, self.sender_id if author == self.sender else comment.get("author_id")
            )
            if person["id"] not in introduced:
                messages.append(person_introduction(person))
                introduced.add(person["id"])
            messages.append(
                human_input(
                    _fenced(author, str(comment.get("body", "")), trusted),
                    {
                        "sender_id": person["id"],
                        "surface": "github",
                        "kind": "human",
                        "data": {
                            "message_type": str(comment.get("type", "comment")),
                            "created_at": str(comment.get("created_at", "")),
                            "comment_id": str(comment.get("comment_id", "")),
                        },
                    },
                )
            )
        return {"messages": messages}

    async def comments(self) -> list[Comment]:
        """The issue's comments, oldest first, as the App reads them; those it could read."""
        url = f"{_API}/repos/{self.owner}/{self.name}/issues/{self.number}/comments"
        listed: list[object] = []
        async with httpx2.AsyncClient(timeout=_TIMEOUT) as client:
            headers = {**_HEADERS, "Authorization": f"Bearer {await _app_token()}"}
            for page in range(1, _MAX_PAGES + 1):
                try:
                    response = await client.get(
                        url, headers=headers, params={"per_page": _PER_PAGE, "page": page}
                    )
                    if response.status_code != 200:  # noqa: PLR2004
                        logger.warning(
                            "GitHub answered %s reading an issue's comments", response.status_code
                        )
                        break
                    page_items = response.json()
                except Exception as exc:  # noqa: BLE001 - the run starts with what was read
                    logger.warning(
                        "Reading a GitHub issue's comments failed: %s", type(exc).__name__
                    )
                    break
                if not isinstance(page_items, list) or not page_items:
                    break
                listed += page_items
                if len(page_items) < _PER_PAGE:
                    break
        return [
            {
                "body": item.get("body", ""),
                "author": _fields(item.get("user")).get("login", "unknown"),
                "created_at": item.get("created_at", ""),
                "comment_id": item.get("id"),
            }
            for item in (_fields(entry) for entry in listed)
        ]

    async def acknowledge(self) -> None:
        """React 👀 to the comment, as the App, so its author sees Open SWE took it.

        A courtesy: a reaction that fails is logged, never raised.
        """
        comment_id = self.comment_id
        if self.event != "issue_comment" or not isinstance(comment_id, int):
            return
        url = f"{_API}/repos/{self.owner}/{self.name}/issues/comments/{comment_id}/reactions"
        try:
            async with httpx2.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(
                    url,
                    headers={**_HEADERS, "Authorization": f"Bearer {await _app_token()}"},
                    json={"content": "eyes"},
                )
        except Exception as exc:  # noqa: BLE001 - a courtesy never fails the run
            logger.warning("Reacting to a GitHub issue comment failed: %s", type(exc).__name__)
            return
        # 200: already reacted; 201: reacted now.
        if response.status_code not in {200, 201}:
            logger.warning("GitHub answered %s reacting to a comment", response.status_code)


async def _app_token() -> str:
    """The App's installation token, which MDA keeps fresh."""
    return await connections.get(GITHUB_TOKEN, {"type": "agent"})
