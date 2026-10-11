"""Behavior of Notion task events: assignment, the design gate, and comment follow-ups."""

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from pydantic import JsonValue

from openswe.notion import notifications
from openswe.notion import webhook as service
from openswe.notion.models import WebhookEvent
from openswe.thread_ids import notion_discussion_thread_id, notion_page_thread_id
from tests.conftest import FakeStore
from tests.notion.fakes import (
    ALICE,
    ASSIGNEE_ID,
    DESIGN_STATUS_ID,
    DOCS_DS,
    INTEGRATION_BOT,
    JARVIS,
    STATUS_ID,
    TASKS_DS,
    FakeNotion,
    comment,
    design_page,
    paragraph,
    task_page,
)

TASK = "1111aaaa-0000-0000-0000-000000000001"
OTHER_TASK = "1111aaaa-0000-0000-0000-000000000002"
DOC = "2222bbbb-0000-0000-0000-000000000001"
OTHER_DOC = "2222bbbb-0000-0000-0000-000000000002"
BOB = "3333cccc-0000-0000-0000-000000000001"
REPO_ID = "CJ%40j"


@dataclass
class Harness:
    notion: FakeNotion
    dispatched: list[dict[str, object]] = field(default_factory=list)
    existing_threads: set[str] = field(default_factory=set)
    allowed: bool = True
    emails_resolved: list[str] = field(default_factory=list)

    def prompts(self) -> list[str]:
        texts: list[str] = []
        for run in self.dispatched:
            run_input = run["input"]
            assert isinstance(run_input, dict)
            for message in run_input["messages"]:
                content = message["content"]
                texts.append(content if isinstance(content, str) else str(content))
        return texts


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore) -> Harness:
    del fake_store
    notion = FakeNotion()
    h = Harness(notion)
    monkeypatch.setenv("NOTION_API_KEY", "secret")
    monkeypatch.setenv("NOTION_AGENT_USER_IDS", JARVIS)
    monkeypatch.setenv("NOTION_TASKS_DATA_SOURCE_ID", TASKS_DS)
    monkeypatch.setenv("NOTION_DOCUMENTS_DATA_SOURCE_ID", DOCS_DS)
    monkeypatch.delenv("NOTION_AGENT_API_KEY", raising=False)
    notion.install(monkeypatch)

    async def dispatch(
        thread_id, content, configurable, *, source, thread_title=None, input=None, metadata=None
    ):
        h.dispatched.append(
            {"thread_id": thread_id, "configurable": configurable, "source": source, "input": input}
        )
        h.existing_threads.add(thread_id)
        return {"run_id": "run-1"}

    async def upsert(thread_id: str, **kwargs: object) -> bool:
        return True

    async def none(*args: object, **kwargs: object) -> None:
        return None

    async def workspace(repo: object) -> str:
        return "default"

    async def exists(thread_id: str) -> bool:
        return thread_id in h.existing_threads

    async def metadata(thread_id: str) -> dict[str, JsonValue] | None:
        if thread_id not in h.existing_threads:
            return None
        return {"repo": {"owner": "aeteq", "name": "sportsbook"}, "source": "notion"}

    async def settings() -> SimpleNamespace:
        return SimpleNamespace(default_repo=None)

    async def login(email: str | None) -> str | None:
        if email:
            h.emails_resolved.append(email)
        return "alice-gh" if email == "alice@aeteq.com" else None

    async def not_busy(thread_id: str) -> bool:
        return False

    monkeypatch.setattr(service.common, "dispatch_agent_run", dispatch)
    monkeypatch.setattr(service.common, "upsert_agent_thread_metadata", upsert)
    monkeypatch.setattr(service.common, "get_thread_workspace", none)
    monkeypatch.setattr(service.common, "workspace_for_repo_config", workspace)
    monkeypatch.setattr(service.common, "thread_exists", exists)
    monkeypatch.setattr(service.common, "get_thread_metadata_safe", metadata)
    monkeypatch.setattr(service.common, "get_workspace_settings", settings)
    monkeypatch.setattr(service.common, "get_profile_default_repo", none)
    monkeypatch.setattr(service.common, "is_repo_allowed", lambda repo: h.allowed)
    monkeypatch.setattr(service.User, "login_for_email", login)
    monkeypatch.setattr(service, "_thread_busy", not_busy)
    return h


def _event(
    event_type: str,
    entity_id: str,
    *,
    updated: list[str] | None = None,
    author: str = ALICE,
    page_id: str | None = None,
) -> WebhookEvent:
    data: dict[str, JsonValue] = {"updated_properties": list(updated or [])}
    if page_id:
        data["page_id"] = page_id
        data["parent"] = {"id": page_id, "type": "page"}
    return WebhookEvent.model_validate(
        {
            "id": "evt-1",
            "type": event_type,
            "entity": {
                "id": entity_id,
                "type": "comment" if event_type.startswith("comment") else "page",
            },
            "authors": [{"id": author, "type": "person"}],
            "data": data,
        }
    )


def _assigned(task_id: str = TASK) -> WebhookEvent:
    return _event("page.properties_updated", task_id, updated=[ASSIGNEE_ID])


def _approved(doc_id: str = DOC) -> WebhookEvent:
    return _event("page.properties_updated", doc_id, updated=[DESIGN_STATUS_ID])


def _status_writes(notion: FakeNotion) -> list[JsonValue]:
    return [props["Status"] for props in notion.property_writes() if "Status" in props]


async def test_assignment_to_agent_dispatches_the_task(harness: Harness) -> None:
    harness.notion.add(task_page(TASK))
    harness.notion.blocks[TASK] = [paragraph("Show the build version in the footer.")]

    await service.handle_notion_event(_assigned())

    assert len(harness.dispatched) == 1
    run = harness.dispatched[0]
    assert run["thread_id"] == notion_page_thread_id(TASK)
    assert run["source"] == "notion"
    configurable = run["configurable"]
    assert isinstance(configurable, dict)
    assert configurable["repo"] == {"owner": "aeteq", "name": "sportsbook"}
    assert configurable["github_login"] == "alice-gh"
    assert configurable["notion_page"]["identifier"] == "DIG-78"
    prompt = "\n".join(harness.prompts())
    assert "Show the build version in the footer." in prompt
    assert "DIG-78" in prompt
    assert _status_writes(harness.notion) == [{"status": {"name": "In progress"}}]


async def test_thread_id_matches_dashed_and_undashed_page_ids() -> None:
    assert notion_page_thread_id(TASK) == notion_page_thread_id(TASK.replace("-", "").upper())


async def test_property_update_that_does_not_touch_the_assignee_is_ignored(
    harness: Harness,
) -> None:
    harness.notion.add(task_page(TASK))

    await service.handle_notion_event(_event("page.properties_updated", TASK, updated=[STATUS_ID]))

    assert harness.dispatched == []


@pytest.mark.parametrize(
    ("assignees", "status"),
    [([ALICE], "Not started"), ([JARVIS], "In review"), ([JARVIS], "Done")],
)
async def test_task_not_for_the_agent_or_past_review_is_ignored(
    harness: Harness, assignees: list[str], status: str
) -> None:
    harness.notion.add(task_page(TASK, assignees=assignees, status=status))

    await service.handle_notion_event(_assigned())

    assert harness.dispatched == []
    assert harness.notion.comment_writes() == []


async def test_page_created_already_assigned_dispatches(harness: Harness) -> None:
    harness.notion.add(task_page(TASK))

    await service.handle_notion_event(_event("page.created", TASK))

    assert len(harness.dispatched) == 1


async def test_disallowed_repository_comments_instead_of_starting(harness: Harness) -> None:
    harness.notion.add(task_page(TASK, repo="someone/else"))
    harness.allowed = False

    await service.handle_notion_event(_assigned())

    assert harness.dispatched == []
    assert any("someone/else" in text for text in harness.notion.comment_writes())


async def test_missing_repository_asks_for_one(harness: Harness) -> None:
    harness.notion.add(task_page(TASK, repo=None))

    await service.handle_notion_event(_assigned())

    assert harness.dispatched == []
    assert any("Repository" in text for text in harness.notion.comment_writes())


async def test_setting_the_missing_repository_starts_the_task(harness: Harness) -> None:
    harness.notion.add(task_page(TASK))

    await service.handle_notion_event(_event("page.properties_updated", TASK, updated=[REPO_ID]))

    [run] = harness.dispatched
    assert run["thread_id"] == notion_page_thread_id(TASK)
    assert _status_writes(harness.notion) == [{"status": {"name": "In progress"}}]


async def test_repository_change_on_a_started_task_does_not_restart_it(harness: Harness) -> None:
    harness.notion.add(task_page(TASK, status="In progress"))
    harness.existing_threads.add(notion_page_thread_id(TASK))

    await service.handle_notion_event(_event("page.properties_updated", TASK, updated=[REPO_ID]))

    assert harness.dispatched == []


async def test_unapproved_design_blocks_the_task_with_a_comment(harness: Harness) -> None:
    harness.notion.add(task_page(TASK, designs=[DOC]))
    harness.notion.add(design_page(DOC, status="In Review", title="Tech Design: versions"))

    await service.handle_notion_event(_assigned())

    assert harness.dispatched == []
    [gate] = harness.notion.comment_writes()
    assert "DIG-78" in gate
    assert "Tech Design: versions" in gate
    assert "In Review" in gate
    assert _status_writes(harness.notion) == []


async def test_approved_designs_let_the_task_start(harness: Harness) -> None:
    harness.notion.add(task_page(TASK, designs=[DOC]))
    harness.notion.add(design_page(DOC, status="Approved"))

    await service.handle_notion_event(_assigned())

    assert len(harness.dispatched) == 1


async def test_design_approval_starts_a_waiting_task_as_its_assigner(harness: Harness) -> None:
    harness.notion.users[BOB] = {"object": "user", "id": BOB, "type": "person", "name": "Bob"}
    harness.notion.add(task_page(TASK, designs=[DOC], created_by=BOB))
    harness.notion.add(design_page(DOC, status="In Review"))
    await service.handle_notion_event(_assigned())
    assert harness.dispatched == []

    harness.notion.add(design_page(DOC, status="Approved"))
    harness.emails_resolved.clear()
    await service.handle_notion_event(
        _event("page.properties_updated", DOC, updated=[DESIGN_STATUS_ID], author=BOB)
    )

    assert len(harness.dispatched) == 1
    configurable = harness.dispatched[0]["configurable"]
    assert isinstance(configurable, dict)
    assert configurable["github_login"] == "alice-gh"
    assert harness.emails_resolved == ["alice@aeteq.com"]


async def test_design_approval_continues_an_in_progress_task_in_its_thread(
    harness: Harness,
) -> None:
    harness.notion.add(task_page(TASK, designs=[DOC], status="In progress"))
    harness.notion.add(design_page(DOC, status="Approved", title="Tech Design: versions"))
    harness.existing_threads.add(notion_page_thread_id(TASK))

    await service.handle_notion_event(_approved())

    assert len(harness.dispatched) == 1
    [prompt] = [text for text in harness.prompts() if "Tech Design: versions" in text]
    assert "now Approved" in prompt


async def test_design_approval_starts_an_in_progress_task_without_a_thread(
    harness: Harness,
) -> None:
    harness.notion.add(task_page(TASK, designs=[DOC], status="In progress"))
    harness.notion.add(design_page(DOC, status="Approved"))

    await service.handle_notion_event(_approved())

    assert len(harness.dispatched) == 1
    assert any("Please work on the following Notion task" in text for text in harness.prompts())


async def test_design_approval_skips_tasks_past_review_or_not_for_the_agent(
    harness: Harness,
) -> None:
    harness.notion.add(task_page(TASK, designs=[DOC], status="In review"))
    harness.notion.add(task_page(OTHER_TASK, designs=[DOC], assignees=[ALICE]))
    harness.notion.add(design_page(DOC, status="Approved"))

    await service.handle_notion_event(_approved())

    assert harness.dispatched == []


async def test_design_approval_keeps_blocking_on_another_unapproved_design(
    harness: Harness,
) -> None:
    harness.notion.add(task_page(TASK, designs=[DOC, OTHER_DOC]))
    harness.notion.add(design_page(DOC, status="Approved"))
    harness.notion.add(design_page(OTHER_DOC, status="Draft", title="Tech Design: API"))

    await service.handle_notion_event(_approved())

    assert harness.dispatched == []
    [gate] = harness.notion.comment_writes()
    assert "Tech Design: API" in gate


async def test_design_status_change_other_than_approval_is_ignored(harness: Harness) -> None:
    harness.notion.add(task_page(TASK, designs=[DOC]))
    harness.notion.add(design_page(DOC, status="In Review"))

    await service.handle_notion_event(_approved())

    assert harness.dispatched == []
    assert harness.notion.comment_writes() == []


async def test_comment_on_a_worked_task_is_forwarded_to_its_thread(harness: Harness) -> None:
    harness.notion.add(task_page(TASK, status="In progress"))
    harness.existing_threads.add(notion_page_thread_id(TASK))
    harness.notion.comments[TASK.replace("-", "")] = [comment("c-1", "Use semver, please")]

    await service.handle_notion_event(_event("comment.created", "c-1", page_id=TASK))

    assert len(harness.dispatched) == 1
    assert any("Use semver, please" in text for text in harness.prompts())


async def test_comment_on_a_page_without_a_thread_is_ignored(harness: Harness) -> None:
    harness.notion.add(task_page(TASK))
    harness.notion.comments[TASK.replace("-", "")] = [comment("c-1", "Hello")]

    await service.handle_notion_event(_event("comment.created", "c-1", page_id=TASK))

    assert harness.dispatched == []


async def test_integration_comment_is_not_forwarded(harness: Harness) -> None:
    harness.notion.add(task_page(TASK, status="In progress"))
    harness.existing_threads.add(notion_page_thread_id(TASK))
    harness.notion.comments[TASK.replace("-", "")] = [
        comment("c-1", "✅ Pull request opened", author=INTEGRATION_BOT)
    ]

    await service.handle_notion_event(_event("comment.created", "c-1", page_id=TASK))

    assert harness.dispatched == []


async def test_record_pull_request_links_the_pr_and_moves_to_review(harness: Harness) -> None:
    harness.notion.add(task_page(TASK, status="In progress"))

    await notifications.record_pull_request(
        TASK, "https://github.com/aeteq/sportsbook/pull/30", opened=True
    )

    [properties] = harness.notion.property_writes()
    assert properties == {
        "Pull Request URL": {"url": "https://github.com/aeteq/sportsbook/pull/30"},
        "Status": {"status": {"name": "In review"}},
    }
    assert harness.notion.comment_writes() == [
        "✅ Pull request opened: https://github.com/aeteq/sportsbook/pull/30"
    ]


async def test_pr_from_a_question_on_the_agent_s_task_is_recorded_on_it(harness: Harness) -> None:
    harness.notion.add(task_page(TASK))

    await notifications.record_pull_request(
        TASK, "https://github.com/aeteq/sportsbook/pull/36", opened=True, agent_task_only=True
    )

    [properties] = harness.notion.property_writes()
    assert properties["Status"] == {"status": {"name": "In review"}}


async def test_pr_from_a_question_on_someone_else_s_task_is_not_recorded(
    harness: Harness,
) -> None:
    harness.notion.add(task_page(TASK, assignees=[ALICE]))

    await notifications.record_pull_request(
        TASK, "https://github.com/aeteq/sportsbook/pull/36", opened=True, agent_task_only=True
    )

    assert harness.notion.property_writes() == []
    assert harness.notion.comment_writes() == []


async def test_linked_pr_is_announced_as_linked_and_keeps_a_done_status(
    harness: Harness,
) -> None:
    harness.notion.add(task_page(TASK, status="Done"))

    await notifications.record_pull_request(
        TASK, "https://github.com/aeteq/sportsbook/pull/30", opened=False
    )

    [properties] = harness.notion.property_writes()
    assert "Status" not in properties
    assert harness.notion.comment_writes() == [
        "✅ Pull request linked: https://github.com/aeteq/sportsbook/pull/30"
    ]


async def test_recording_the_task_s_current_pr_again_is_quiet(harness: Harness) -> None:
    page = task_page(TASK, status="In review")
    page["properties"]["Pull Request URL"]["url"] = "https://github.com/aeteq/sportsbook/pull/30"  # type: ignore[index]
    harness.notion.add(page)

    await notifications.record_pull_request(
        TASK, "https://github.com/aeteq/sportsbook/pull/30", opened=True
    )

    assert harness.notion.comment_writes() == []


async def test_notion_outage_does_not_raise_from_write_back(harness: Harness) -> None:
    harness.notion.add(task_page(TASK, status="In progress"))
    harness.notion.fail_writes = True

    await notifications.record_pull_request(TASK, "https://github.com/a/b/pull/1", opened=True)

    assert await notifications.post_notion_comment(TASK, "hi") is False


async def test_writes_use_the_agent_token_and_appear_as_the_agent(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTION_AGENT_API_KEY", "jarvis-pat")
    harness.notion.add(task_page(TASK, status="In progress"))

    await notifications.record_pull_request(
        TASK, "https://github.com/aeteq/sportsbook/pull/30", opened=True
    )

    assert set(harness.notion.write_tokens) == {"jarvis-pat"}
    [comment_body] = [
        body for method, path, body in harness.notion.writes if path == "/v1/comments"
    ]
    assert comment_body["display_name"] == {"type": "user"}


async def test_without_an_agent_token_writes_use_the_integration(harness: Harness) -> None:
    harness.notion.add(task_page(TASK))

    await notifications.post_notion_comment(TASK, "hello")

    assert harness.notion.write_tokens == ["secret"]
    [(_, _, body)] = harness.notion.writes
    assert "display_name" not in body


async def test_the_agent_member_s_own_comment_is_not_forwarded(harness: Harness) -> None:
    harness.notion.users[JARVIS] = {
        "object": "user",
        "id": JARVIS,
        "type": "person",
        "name": "Jarvis",
        "person": {"email": "jarvis@aeteq.com"},
    }
    harness.notion.add(task_page(TASK, status="In progress"))
    harness.existing_threads.add(notion_page_thread_id(TASK))
    harness.notion.comments[TASK.replace("-", "")] = [
        comment("c-1", "Which footer should show it?", author=JARVIS)
    ]

    await service.handle_notion_event(_event("comment.created", "c-1", page_id=TASK))

    assert harness.dispatched == []


PLAIN_PAGE = "4444dddd-0000-0000-0000-000000000001"


def _plain_page() -> dict[str, JsonValue]:
    return {
        "object": "page",
        "id": PLAIN_PAGE,
        "url": "https://www.notion.so/runbook",
        "in_trash": False,
        "parent": {"type": "page_id", "page_id": "5555eeee-0000-0000-0000-000000000001"},
        "properties": {
            "title": {
                "id": "title",
                "type": "title",
                "title": [{"type": "text", "plain_text": "Release runbook"}],
            }
        },
    }


def _configurable_of(run: dict[str, object]) -> dict[str, object]:
    configurable = run["configurable"]
    assert isinstance(configurable, dict)
    return configurable


async def test_mention_starts_a_question_in_its_discussion(harness: Harness) -> None:
    harness.notion.add(_plain_page())
    harness.notion.blocks[PLAIN_PAGE] = [paragraph("Step 1: tag the release.")]
    harness.notion.comments[PLAIN_PAGE.replace("-", "")] = [
        comment(
            "c-9", "how do we roll back?", page_id=PLAIN_PAGE, discussion_id="d9", mentions=JARVIS
        )
    ]

    await service.handle_notion_event(_event("comment.created", "c-9", page_id=PLAIN_PAGE))

    [run] = harness.dispatched
    assert run["thread_id"] == notion_discussion_thread_id("d9")
    configurable = _configurable_of(run)
    assert "repo" not in configurable
    assert configurable["repo_explicitly_none"] is True
    notion_page = configurable["notion_page"]
    assert isinstance(notion_page, dict)
    assert (notion_page["kind"], notion_page["discussion_id"]) == ("mention", "d9")
    prompts = "\n".join(harness.prompts())
    assert "Release runbook" in prompts
    assert "Step 1: tag the release." in prompts
    assert "how do we roll back?" in prompts
    assert harness.notion.property_writes() == []


async def test_mention_on_a_task_page_uses_its_repository_without_starting_the_task(
    harness: Harness,
) -> None:
    harness.notion.add(task_page(TASK))
    harness.notion.comments[TASK.replace("-", "")] = [
        comment("c-9", "is this blocked?", page_id=TASK, discussion_id="d9", mentions=JARVIS)
    ]

    await service.handle_notion_event(_event("comment.created", "c-9", page_id=TASK))

    [run] = harness.dispatched
    assert run["thread_id"] != notion_page_thread_id(TASK)
    assert _configurable_of(run)["repo"] == {"owner": "aeteq", "name": "sportsbook"}
    assert harness.notion.property_writes() == []


async def test_follow_up_in_a_question_discussion_is_forwarded_without_a_mention(
    harness: Harness,
) -> None:
    harness.notion.add(_plain_page())
    harness.existing_threads.add(notion_discussion_thread_id("d9"))
    harness.notion.comments[PLAIN_PAGE.replace("-", "")] = [
        comment("c-10", "and for hotfixes?", page_id=PLAIN_PAGE, discussion_id="d9")
    ]

    await service.handle_notion_event(_event("comment.created", "c-10", page_id=PLAIN_PAGE))

    [run] = harness.dispatched
    assert run["thread_id"] == notion_discussion_thread_id("d9")
    assert any("and for hotfixes?" in text for text in harness.prompts())


async def test_comment_on_a_worked_task_replies_in_its_discussion(harness: Harness) -> None:
    harness.notion.add(task_page(TASK, status="In progress"))
    harness.existing_threads.add(notion_page_thread_id(TASK))
    harness.notion.comments[TASK.replace("-", "")] = [
        comment("c-11", "use semver", page_id=TASK, discussion_id="d11", mentions=JARVIS)
    ]

    await service.handle_notion_event(_event("comment.created", "c-11", page_id=TASK))

    [run] = harness.dispatched
    assert run["thread_id"] == notion_page_thread_id(TASK)
    notion_page = _configurable_of(run)["notion_page"]
    assert isinstance(notion_page, dict)
    assert notion_page["discussion_id"] == "d11"
    assert "kind" not in notion_page


async def test_reply_goes_into_the_discussion(harness: Harness) -> None:
    await notifications.post_notion_comment(
        PLAIN_PAGE, "Roll back with `make rollback`.", discussion_id="d9"
    )

    [(_, _, body)] = harness.notion.writes
    assert body["discussion_id"] == "d9"
    assert "parent" not in body
