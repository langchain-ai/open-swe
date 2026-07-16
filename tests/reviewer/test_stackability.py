from copy import deepcopy

import pytest

from agent.review.stackability import validate_stackability_review


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
