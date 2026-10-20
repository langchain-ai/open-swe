import sys

import pytest
from pydantic import JsonValue

import openswe.tools.notion_designs  # noqa: F401
from openswe.notion.designs import DesignPublishError, design_template, publish_design
from openswe.notion.settings import normalize_notion_id, page_id_from_reference
from tests.notion.fakes import DOCS_DS, JARVIS, TASKS_DS, FakeNotion, task_page

tool = sys.modules["openswe.tools.notion_designs"]

TASK = "1111aaaa-0000-0000-0000-000000000001"
EXISTING_DOC = "2222bbbb-0000-0000-0000-000000000001"
TEMPLATE = "3dcc0b65a473801da5d2fd8a7be2e94e"


def _docs_schema(
    *, statuses: list[str] | None = None, tags: list[str] | None = None
) -> dict[str, JsonValue]:
    return {
        "id": DOCS_DS,
        "properties": {
            "Doc name": {"id": "title", "name": "Doc name", "type": "title"},
            "Status": {
                "id": "s",
                "name": "Status",
                "type": "status",
                "status": {
                    "options": [
                        {"name": name}
                        for name in (
                            ["Draft", "In Review", "Approved"] if statuses is None else statuses
                        )
                    ]
                },
            },
            "Tag": {
                "id": "t",
                "name": "Tag",
                "type": "multi_select",
                "multi_select": {
                    "options": [
                        {"name": name} for name in (["Tech Design"] if tags is None else tags)
                    ]
                },
            },
        },
    }


@pytest.fixture
def notion(monkeypatch: pytest.MonkeyPatch) -> FakeNotion:
    fake = FakeNotion()
    monkeypatch.setenv("NOTION_API_KEY", "secret")
    monkeypatch.setenv("NOTION_AGENT_USER_IDS", JARVIS)
    monkeypatch.setenv("NOTION_TASKS_DATA_SOURCE_ID", TASKS_DS)
    monkeypatch.setenv("NOTION_DOCUMENTS_DATA_SOURCE_ID", DOCS_DS)
    monkeypatch.setenv("NOTION_DESIGN_TEMPLATE_ID", f"Tech-Design-{TEMPLATE}")
    monkeypatch.delenv("NOTION_AGENT_API_KEY", raising=False)
    fake.schemas[normalize_notion_id(DOCS_DS)] = _docs_schema()
    fake.install(monkeypatch)
    return fake


@pytest.mark.parametrize(
    "title", ["App providers", "Tech Design: App providers", "tech design App providers"]
)
async def test_publishes_the_design_in_review_and_links_it_from_the_task(
    notion: FakeNotion, title: str
) -> None:
    notion.add(task_page(TASK, designs=[EXISTING_DOC]))

    design = await publish_design(TASK, title, "## 1. Context\nWhy.")

    [created] = notion.created_pages()
    assert created["parent"] == {
        "type": "data_source_id",
        "data_source_id": normalize_notion_id(DOCS_DS),
    }
    assert created["markdown"] == "## 1. Context\nWhy."
    assert created["icon"] == {"type": "emoji", "emoji": "📐"}
    properties = created["properties"]
    assert isinstance(properties, dict)
    assert properties["Doc name"] == {
        "title": [{"type": "text", "text": {"content": "Tech Design: App providers"}}]
    }
    assert properties["Status"] == {"status": {"name": "In Review"}}
    assert properties["Tag"] == {"multi_select": [{"name": "Tech Design"}]}
    assert design.title == "Tech Design: App providers"
    assert notion.property_writes() == [
        {"Design": {"relation": [{"id": EXISTING_DOC}, {"id": design.id}]}}
    ]


async def test_missing_review_status_and_tag_options_are_left_unset(notion: FakeNotion) -> None:
    notion.schemas[normalize_notion_id(DOCS_DS)] = _docs_schema(statuses=["Draft"], tags=[])
    notion.add(task_page(TASK))

    await publish_design(TASK, "App providers", "Body")

    [created] = notion.created_pages()
    properties = created["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {"Doc name"}


async def test_a_task_without_a_design_relation_gets_no_design(notion: FakeNotion) -> None:
    page = task_page(TASK)
    del page["properties"]["Design"]  # type: ignore[attr-defined]
    notion.add(page)

    with pytest.raises(DesignPublishError, match="relation"):
        await publish_design(TASK, "App providers", "Body")

    assert notion.created_pages() == []


async def test_reads_the_template_named_by_a_page_slug(notion: FakeNotion) -> None:
    notion.markdown[TEMPLATE] = "## 1. Context\n*What is broken*"

    template = await design_template()

    assert template is not None
    assert template.markdown == "## 1. Context\n*What is broken*"


async def test_no_template_configured(notion: FakeNotion, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTION_DESIGN_TEMPLATE_ID")

    assert await design_template() is None


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        (TEMPLATE, TEMPLATE),
        ("3dcc0b65-a473-801d-a5d2-fd8a7be2e94e", TEMPLATE),
        (f"Tech-Design-{TEMPLATE}", TEMPLATE),
        (f"https://app.notion.com/p/Tech-Design-{TEMPLATE}?pvs=4#heading", TEMPLATE),
        ("Tech Design", ""),
        ("", ""),
    ],
)
def test_page_id_from_reference(reference: str, expected: str) -> None:
    assert page_id_from_reference(reference) == expected


@pytest.mark.parametrize(
    "notion_page",
    [None, {"id": TASK, "kind": "mention", "discussion_id": "d9"}],
)
async def test_tool_only_publishes_for_an_assigned_task(
    notion: FakeNotion, monkeypatch: pytest.MonkeyPatch, notion_page: dict[str, str] | None
) -> None:
    notion.add(task_page(TASK))
    configurable: dict[str, object] = {"source": "notion" if notion_page else "slack"}
    if notion_page:
        configurable["notion_page"] = notion_page
    monkeypatch.setattr("openswe.run_config.get_config", lambda: {"configurable": configurable})

    result = await tool.create_notion_design("App providers", "Body")

    assert result["success"] is False
    assert notion.created_pages() == []
