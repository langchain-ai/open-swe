from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest

from agent.review.stackability import (
    get_stackability_review,
    new_stackability_review_record,
    render_stackability_advisory,
    render_stackability_blocking_body,
    render_stackability_dashboard_markdown,
    set_stackability_review,
    update_stackability_review,
    validate_stackability_review,
)


def _split_review() -> dict[str, object]:
    return {
        "verdict": "split_recommended",
        "confidence": "high",
        "rationale": "The migration and its consumers can land and be tested independently.",
        "proposed_stack": [
            {
                "title": "Add the storage migration",
                "purpose": "Introduce the schema used by the new feature.",
                "include": ["migrations/", "tests/migrations/"],
                "exclude_or_defer": ["agent/feature.py"],
                "depends_on": None,
                "independently_testable_because": "Migration tests exercise the schema alone.",
                "suggested_checks": ["uv run pytest tests/migrations"],
            },
            {
                "title": "Adopt the migrated schema",
                "purpose": "Move application reads and writes to the new schema.",
                "include": ["agent/feature.py", "tests/test_feature.py"],
                "exclude_or_defer": [],
                "depends_on": "Add the storage migration",
                "independently_testable_because": "Feature tests cover the application behavior.",
                "suggested_checks": ["uv run pytest tests/test_feature.py"],
            },
        ],
        "harness_prompt": "Create the two ordered PRs above without force-pushing the source branch.",
        "risks_or_human_decisions": ["Confirm the migration can precede the application rollout."],
    }


def test_valid_split_recommended_review() -> None:
    assert validate_stackability_review(_split_review()) == []


def test_valid_not_worth_splitting_review() -> None:
    review = _split_review()
    review.update(
        verdict="not_worth_splitting",
        confidence="medium",
        proposed_stack=[],
        rationale="The implementation and tests form one cohesive behavior change.",
    )

    assert validate_stackability_review(review) == []


def test_valid_needs_human_context_review_with_question() -> None:
    review = _split_review()
    review.update(
        verdict="needs_human_context",
        confidence="low",
        proposed_stack=[],
        risks_or_human_decisions=[
            "Should the migration be deployed before this feature is enabled?"
        ],
    )

    assert validate_stackability_review(review) == []


@pytest.mark.parametrize(
    ("updates", "expected_error"),
    [
        ({"verdict": "maybe"}, "verdict: must be one of"),
        ({"verdict": []}, "verdict: must be one of"),
        ({"confidence": "certain"}, "confidence: must be one of"),
        ({"rationale": "  "}, "rationale: must be a non-empty string"),
        ({"harness_prompt": None}, "harness_prompt: must be a non-empty string"),
        ({"risks_or_human_decisions": "none"}, "risks_or_human_decisions: must be a list"),
        ({"proposed_stack": {}}, "proposed_stack: must be a list"),
    ],
)
def test_invalid_top_level_fields(updates: dict[str, object], expected_error: str) -> None:
    review = _split_review()
    review.update(updates)

    assert any(error.startswith(expected_error) for error in validate_stackability_review(review))


def test_missing_required_fields_are_reported() -> None:
    errors = validate_stackability_review({})

    for field in (
        "verdict",
        "confidence",
        "rationale",
        "proposed_stack",
        "harness_prompt",
        "risks_or_human_decisions",
    ):
        assert any(error.startswith(f"{field}:") for error in errors)


def test_malformed_step_fields_are_all_reported() -> None:
    review = _split_review()
    review["proposed_stack"] = [
        {
            "title": " ",
            "purpose": 3,
            "include": ["agent/", 4],
            "exclude_or_defer": "later",
            "depends_on": 1,
            "independently_testable_because": "",
            "suggested_checks": [],
        },
        "not a step",
    ]

    errors = validate_stackability_review(review)

    expected_paths = (
        "proposed_stack[0].title",
        "proposed_stack[0].purpose",
        "proposed_stack[0].include[1]",
        "proposed_stack[0].exclude_or_defer",
        "proposed_stack[0].depends_on",
        "proposed_stack[0].independently_testable_because",
        "proposed_stack[0].suggested_checks",
        "proposed_stack[1]",
    )
    for path in expected_paths:
        assert any(error.startswith(f"{path}:") for error in errors)


def test_duplicate_step_titles_are_rejected_after_trimming() -> None:
    review = _split_review()
    stack = review["proposed_stack"]
    assert isinstance(stack, list)
    assert isinstance(stack[1], dict)
    stack[1]["title"] = "  Add the storage migration  "

    assert any(
        error.startswith("proposed_stack[1].title: must be unique")
        for error in validate_stackability_review(review)
    )


@pytest.mark.parametrize(
    ("step_index", "depends_on"),
    [
        (1, "Missing step"),
        (0, "Adopt the migrated schema"),
        (0, "Add the storage migration"),
    ],
)
def test_step_dependencies_must_reference_an_earlier_step(step_index: int, depends_on: str) -> None:
    review = _split_review()
    stack = review["proposed_stack"]
    assert isinstance(stack, list)
    assert isinstance(stack[step_index], dict)
    stack[step_index]["depends_on"] = depends_on

    assert (
        f"proposed_stack[{step_index}].depends_on: must reference an earlier step title"
        in validate_stackability_review(review)
    )


def test_step_dependencies_match_titles_after_normalization() -> None:
    review = _split_review()
    stack = review["proposed_stack"]
    assert isinstance(stack, list)
    assert isinstance(stack[1], dict)
    stack[1]["depends_on"] = "  ADD THE STORAGE MIGRATION  "

    assert validate_stackability_review(review) == []


def test_split_recommended_requires_at_least_two_steps() -> None:
    review = _split_review()
    stack = review["proposed_stack"]
    assert isinstance(stack, list)
    review["proposed_stack"] = stack[:1]

    assert (
        "proposed_stack: split_recommended requires at least two steps"
        in validate_stackability_review(review)
    )


def test_not_worth_splitting_rejects_proposed_steps() -> None:
    review = _split_review()
    review["verdict"] = "not_worth_splitting"

    assert (
        "proposed_stack: not_worth_splitting requires no proposed steps"
        in validate_stackability_review(review)
    )


def test_needs_human_context_requires_an_explicit_item() -> None:
    review = _split_review()
    review.update(verdict="needs_human_context", proposed_stack=[], risks_or_human_decisions=[])

    assert (
        "risks_or_human_decisions: needs_human_context requires at least one explicit risk, "
        "decision, or question"
    ) in validate_stackability_review(review)


def test_validation_does_not_mutate_input() -> None:
    review = _split_review()
    original = deepcopy(review)

    validate_stackability_review(review)

    assert review == original


def test_new_record_defaults_to_unpublished() -> None:
    review = _split_review()

    record = new_stackability_review_record("abc123", review)

    assert record["reviewed_head_sha"] == "abc123"
    assert record["review"] == review
    assert record["publication"] == {
        "mode": None,
        "state": "unpublished",
        "github_comment_id": None,
        "github_review_id": None,
        "github_review_thread_id": None,
    }


@pytest.mark.asyncio
async def test_set_and_get_stackability_review_use_separate_metadata_key() -> None:
    record = new_stackability_review_record("abc123", _split_review())
    fake_client = AsyncMock()
    fake_client.threads.get.return_value = {
        "metadata": {"findings": [{"id": "f_existing"}], "stackability_review": record}
    }

    with patch("agent.review.findings.get_client", return_value=fake_client):
        await set_stackability_review("tid", record)
        loaded = await get_stackability_review("tid")

    fake_client.threads.update.assert_awaited_once_with(
        thread_id="tid",
        metadata={"kind": "reviewer", "stackability_review": record},
    )
    assert loaded == record


@pytest.mark.asyncio
async def test_get_stackability_review_returns_none_for_missing_or_malformed_metadata() -> None:
    fake_client = AsyncMock()
    with patch("agent.review.findings.get_client", return_value=fake_client):
        fake_client.threads.get.return_value = {"metadata": {}}
        assert await get_stackability_review("tid") is None

        fake_client.threads.get.return_value = {
            "metadata": {"stackability_review": {"reviewed_head_sha": "abc123"}}
        }
        assert await get_stackability_review("tid") is None


@pytest.mark.asyncio
async def test_update_stackability_review_preserves_assessment_and_sha() -> None:
    record = new_stackability_review_record("abc123", _split_review())
    fake_client = AsyncMock()
    fake_client.threads.get.return_value = {"metadata": {"stackability_review": record}}

    with patch("agent.review.findings.get_client", return_value=fake_client):
        updated = await update_stackability_review(
            "tid",
            publication={
                "mode": "manual_advisory",
                "state": "published",
                "github_comment_id": 42,
                "github_review_id": None,
                "github_review_thread_id": None,
            },
        )

    assert updated is not None
    assert updated["reviewed_head_sha"] == "abc123"
    assert updated["review"] == record["review"]
    assert updated["publication"]["github_comment_id"] == 42
    fake_client.threads.update.assert_awaited_once_with(
        thread_id="tid",
        metadata={"kind": "reviewer", "stackability_review": updated},
    )


def test_stackability_rendering_is_deterministic_and_complete() -> None:
    record = new_stackability_review_record("abc123", _split_review())

    advisory = render_stackability_advisory(record)
    blocking = render_stackability_blocking_body(record)
    dashboard = render_stackability_dashboard_markdown(record)

    assert advisory == render_stackability_advisory(record)
    assert blocking == render_stackability_blocking_body(record)
    assert advisory.count("<!-- open-swe-stackability-review -->") == 1
    assert blocking.count("<!-- open-swe-stackability-review -->") == 1
    assert "non-blocking" in advisory.lower()
    assert "Add the storage migration" in advisory
    assert "migrations/" in advisory
    assert "agent/feature.py" in advisory
    assert "Migration tests exercise the schema alone." in advisory
    assert "uv run pytest tests/migrations" in advisory
    assert "Confirm the migration can precede" in advisory
    assert "Create the two ordered PRs" in advisory
    assert "Action required" in blocking
    assert "Reviewed head: `abc123`" in dashboard
    assert "<!-- open-swe-stackability-review -->" not in dashboard


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        ("split_recommended", "Split recommended"),
        ("not_worth_splitting", "Not worth splitting"),
        ("needs_human_context", "Human context needed"),
    ],
)
def test_dashboard_rendering_labels_each_verdict(verdict: str, expected: str) -> None:
    review = _split_review()
    review["verdict"] = verdict
    if verdict != "split_recommended":
        review["proposed_stack"] = []
    if verdict == "needs_human_context":
        review["risks_or_human_decisions"] = ["Which rollout order should be preserved?"]

    rendered = render_stackability_dashboard_markdown(
        new_stackability_review_record("abc123", review)
    )

    assert expected in rendered
