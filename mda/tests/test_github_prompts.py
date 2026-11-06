"""The prompts MDA's GitHub handler writes are the ones the backend wrote for GitHub runs.

``github_prompt_backend.json`` holds what the backend's ``prepare_github_issue_run``
wrote for each issue mention, and its ``_github_event_prompt`` for each automation
event, before both moved here; ``mda/`` tests can't import the backend.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from rt.github_automations import SubjectEvent
from rt.github_issues import IssueMention
from runtime_api.github import GitHubAutomation, GitHubAutomationRules

_BACKEND = json.loads((Path(__file__).parent / "github_prompt_backend.json").read_text())


def _json(value: object) -> Any:
    return json.loads(json.dumps(value))


@pytest.mark.parametrize("name", sorted(_BACKEND["issues"]))
def test_an_issue_mention_is_written_as_the_backend_wrote_it(name: str) -> None:
    case = _BACKEND["issues"][name]
    mention = IssueMention.of(case["event"], case["payload"])
    assert mention is not None
    trusted = frozenset(case["trusted"])

    if case["started"]:
        written = mention.follow_up(trusted)
    else:
        written = mention.first(mention.with_comment(_json(case["comments"])), trusted)

    assert _json(written) == case["expected"]


@pytest.mark.parametrize("name", sorted(_BACKEND["automations"]))
def test_an_automation_prompt_is_written_as_the_backend_wrote_it(name: str) -> None:
    case = _BACKEND["automations"][name]
    subject = SubjectEvent.of(case["event_type"], case["payload"])
    assert subject is not None
    automation = GitHubAutomation(id="a-1", prompt=case["prompt"], events=[case["event"]])

    [fired] = subject.fired(
        GitHubAutomationRules(automations=[automation], author_registered=case["registered"])
    )

    assert (fired.event, fired.prompt) == (case["event"], case["expected"])


def _mention(**payload: Any) -> IssueMention:
    mention = IssueMention.of(
        "issue_comment",
        {
            "issue": {"id": 1, "number": 7, "title": "t", "user": {"login": "bob"}},
            "repository": {"name": "api", "owner": {"login": "acme"}},
            "sender": {"login": "alice", "id": 11},
            **payload,
        },
    )
    assert mention is not None
    return mention


def test_the_people_a_mention_quotes_are_the_backends_lookup() -> None:
    mention = _mention(comment={"id": 5, "user": {"login": "carol"}})

    assert mention.logins() == ["alice", "bob", "carol"]
    # Each once, the sender first; a name GitHub can't spell is no one's.
    assert mention.logins(
        [{"author": "dave"}, {"author": "bob"}, {"author": "unknown user"}, {"author": "x[bot]"}]
    ) == ["alice", "bob", "carol", "dave", "x[bot]"]


@pytest.mark.parametrize(
    "payload",
    [
        {"issue": {"number": 7}},
        {"issue": {"id": 1}},
        {"repository": {"name": "api/../../x", "owner": {"login": "acme"}}},
    ],
    ids=["no-id", "no-number", "bad-repository"],
)
def test_a_mention_without_its_issue_or_repository_is_none(payload: dict[str, Any]) -> None:
    base: dict[str, Any] = {
        "issue": {"id": 1, "number": 7},
        "repository": {"name": "api", "owner": {"login": "acme"}},
    }
    assert IssueMention.of("issues", base | payload) is None


@pytest.mark.parametrize(
    ("event_type", "payload", "events"),
    [
        ("issues", {"action": "opened"}, {"issues.opened"}),
        ("issues", {"action": "edited"}, None),
        ("pull_request", {"action": "opened"}, {"pull_request.opened"}),
        ("pull_request", {"action": "closed"}, {"pull_request.closed"}),
        (
            "pull_request",
            {"action": "closed", "pull_request": {"merged": True}},
            {"pull_request.closed", "pull_request.merged"},
        ),
        ("pull_request", {"action": "synchronize"}, None),
    ],
)
def test_the_trigger_events_a_delivery_stands_for(
    event_type: str, payload: dict[str, Any], events: set[str] | None
) -> None:
    subject = SubjectEvent.of(event_type, payload)
    assert (subject.events if subject else None) == events


def test_a_merge_fires_on_the_most_specific_event_once() -> None:
    subject = SubjectEvent.of(
        "pull_request", {"action": "closed", "pull_request": {"merged": True}}
    )
    assert subject is not None
    both = GitHubAutomation(
        id="both", prompt="p", events=["pull_request.closed", "pull_request.merged"]
    )
    closed = GitHubAutomation(id="closed", prompt="p", events=["pull_request.closed"])
    other = GitHubAutomation(id="other", prompt="p", events=["issues.opened"])

    fired = subject.fired(GitHubAutomationRules(automations=[both, closed, other]))

    assert [(each.automation_id, each.event) for each in fired] == [
        ("both", "pull_request.merged"),
        ("closed", "pull_request.closed"),
    ]
