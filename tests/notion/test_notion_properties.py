"""Reading and writing Notion task properties as the real Tasks schema shapes them."""

import pytest
from pydantic import JsonValue

from openswe.notion.models import NotionDataSource, NotionPage
from openswe.notion.properties import (
    page_identifier,
    property_was_updated,
    repo_config,
    status_patch,
)
from tests.notion.fakes import ASSIGNEE_ID, task_page

TASK = "1111aaaa-0000-0000-0000-000000000001"


def _page(**overrides: JsonValue) -> NotionPage:
    raw = task_page(TASK)
    raw["properties"].update(overrides)  # type: ignore[union-attr]
    return NotionPage.model_validate(raw)


@pytest.mark.parametrize(
    ("prop", "expected"),
    [
        ({"type": "select", "select": {"name": "aeteq/sportsbook"}}, ("aeteq", "sportsbook")),
        ({"type": "select", "select": {"name": "sportsbook"}}, ("aeteq", "sportsbook")),
        ({"type": "select", "select": {"name": "reporting-api"}}, ("aeteq", "reporting-api")),
        ({"type": "select", "select": {"name": "repo-tools"}}, ("aeteq", "repo-tools")),
        ({"type": "select", "select": {"name": "repo:aeteq/infra"}}, ("aeteq", "infra")),
        (
            {"type": "rich_text", "rich_text": [{"plain_text": "aeteq/open-swe"}]},
            ("aeteq", "open-swe"),
        ),
        ({"type": "url", "url": "https://github.com/aeteq/infa"}, ("aeteq", "infa")),
        ({"type": "select", "select": None}, None),
    ],
)
def test_repository_property_resolves_to_a_repo(
    prop: dict[str, JsonValue], expected: tuple[str, str] | None
) -> None:
    repo = repo_config(_page(Repository={"id": "CJ%40j", **prop}), "Repository", "aeteq")

    assert ((repo["owner"], repo["name"]) if repo else None) == expected


def test_identifier_comes_from_the_unique_id_property() -> None:
    assert page_identifier(_page()) == "DIG-78"


def test_updated_property_ids_match_regardless_of_url_encoding() -> None:
    page = _page()
    assert property_was_updated(page, "Assignee", [ASSIGNEE_ID])
    assert property_was_updated(page, "Assignee", ["@JSP"])
    assert not property_was_updated(page, "Assignee", ["AJ%3Fv"])


def _schema(options: list[str]) -> NotionDataSource:
    return NotionDataSource.model_validate(
        {
            "id": "ds",
            "properties": {
                "Status": {
                    "type": "status",
                    "status": {"options": [{"name": name} for name in options]},
                }
            },
        }
    )


def test_status_patch_uses_the_property_type() -> None:
    status_page = _page()
    select_page = _page(Status={"id": "s", "type": "select", "select": {"name": "Todo"}})

    assert status_patch(status_page, "Status", "In review", None) == {
        "Status": {"status": {"name": "In review"}}
    }
    assert status_patch(select_page, "Status", "In review", None) == {
        "Status": {"select": {"name": "In review"}}
    }


def test_status_patch_skips_an_option_the_schema_lacks() -> None:
    page = _page()

    assert status_patch(page, "Status", "In review", _schema(["Not started", "Done"])) is None
    assert status_patch(page, "Status", "Done", _schema(["Not started", "Done"])) is not None
