from copy import deepcopy
from typing import Any, Literal, TypedDict, cast

from .findings import get_thread_metadata, set_reviewer_thread_metadata

StackabilityVerdict = Literal[
    "split_recommended",
    "not_worth_splitting",
    "needs_human_context",
]
StackabilityConfidence = Literal["low", "medium", "high"]
StackabilityPublishingMode = Literal["manual_advisory", "automatic_advisory", "blocking"]
StackabilityPublicationState = Literal["unpublished", "published"]


class StackStep(TypedDict):
    title: str
    purpose: str
    include: list[str]
    exclude_or_defer: list[str]
    depends_on: str | None
    independently_testable_because: str
    suggested_checks: list[str]


class StackabilityReview(TypedDict):
    verdict: StackabilityVerdict
    confidence: StackabilityConfidence
    rationale: str
    proposed_stack: list[StackStep]
    harness_prompt: str
    risks_or_human_decisions: list[str]


class StackabilityPublication(TypedDict):
    mode: StackabilityPublishingMode | None
    state: StackabilityPublicationState
    github_comment_id: int | None
    github_review_id: int | None
    github_review_thread_id: str | None


class StoredStackabilityReview(TypedDict):
    reviewed_head_sha: str
    review: StackabilityReview
    publication: StackabilityPublication


STACKABILITY_REVIEW_RUBRIC = """
Assess whether splitting the pull request creates a meaningfully safer or easier review and landing
sequence. This is not a line-count warning and is separate from bug findings, which require a
concrete, diff-anchored failure mode.

Verdicts:
- split_recommended: propose two or more ordered, independently testable steps when separation
  improves reviewer comprehension, safe landing order, accidental-coupling control, or
  codeowner/domain ownership clarity.
- not_worth_splitting: use for a cohesive change whose implementation, tests, generated artifacts,
  or mechanical edits are best reviewed and landed together, even when the pull request is large.
- needs_human_context: use when an ownership, rollout, compatibility, or product decision prevents a
  responsible recommendation. State each concrete question or decision needed.

Confidence:
- high: the dependency boundaries and checks are directly supported by the pull request.
- medium: the split is useful but some boundary or landing detail requires a reasonable assumption.
- low: important repository, rollout, or ownership context is missing.

For each proposed step, identify its purpose, included paths or scopes, deferred work, dependency on
earlier steps, why it is independently testable, and checks that demonstrate that independence.
Avoid splits that merely distribute line count, break an atomic refactor, separate tests from their
behavior, create temporary broken states, or add review and merge overhead without a clearer unit.

Publication modes are manual_advisory (requested, non-blocking), automatic_advisory (triggered,
non-blocking), and blocking (opt-in and appropriate only for an actionable split or a specific human
context question). A stackability review must never be represented as a normal bug finding.
""".strip()


HARNESS_PROMPT_REQUIREMENTS = """
The harness prompt must preserve the source pull request's repository, base branch, head branch, and
relevant commit context; describe the ordered branches and pull requests to create and the dependency
between each; assign included paths or scopes and explicitly deferred changes to every step; list the
checks that establish independent testability; and carry forward unresolved risks, decisions, and
questions. It must instruct the harness to preserve user work and avoid force-pushing or otherwise
rewriting the source branch. The resulting stack should be created on new branches unless the user
explicitly authorizes another approach.
""".strip()


_VERDICTS = {"split_recommended", "not_worth_splitting", "needs_human_context"}
_CONFIDENCES = {"low", "medium", "high"}
_PUBLISHING_MODES = {"manual_advisory", "automatic_advisory", "blocking"}
_PUBLICATION_STATES = {"unpublished", "published"}
_STACKABILITY_METADATA_KEY = "stackability_review"
STACKABILITY_REVIEW_MARKER = "<!-- open-swe-stackability-review -->"
_REVIEW_FIELDS = (
    "verdict",
    "confidence",
    "rationale",
    "proposed_stack",
    "harness_prompt",
    "risks_or_human_decisions",
)
_STEP_FIELDS = (
    "title",
    "purpose",
    "include",
    "exclude_or_defer",
    "depends_on",
    "independently_testable_because",
    "suggested_checks",
)


def _validate_non_empty_string(value: object, path: str, errors: list[str]) -> bool:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path}: must be a non-empty string")
        return False
    return True


def _validate_string_list(
    value: object, path: str, errors: list[str], *, require_non_empty: bool = False
) -> bool:
    if not isinstance(value, list):
        errors.append(f"{path}: must be a list of strings")
        return False
    if require_non_empty and not value:
        errors.append(f"{path}: must contain at least one string")
    for index, item in enumerate(value):
        _validate_non_empty_string(item, f"{path}[{index}]", errors)
    return True


def _validate_stack_step(
    value: object, index: int, errors: list[str], seen_titles: set[str]
) -> None:
    path = f"proposed_stack[{index}]"
    if not isinstance(value, dict):
        errors.append(f"{path}: must be an object")
        return

    for field in _STEP_FIELDS:
        if field not in value:
            errors.append(f"{path}.{field}: field is required")

    title = value.get("title")
    title_is_valid = _validate_non_empty_string(title, f"{path}.title", errors)
    normalized_title: str | None = None
    if title_is_valid and isinstance(title, str):
        normalized_title = title.strip().casefold()
        if normalized_title in seen_titles:
            errors.append(f"{path}.title: must be unique within proposed_stack")

    _validate_non_empty_string(value.get("purpose"), f"{path}.purpose", errors)
    _validate_string_list(value.get("include"), f"{path}.include", errors, require_non_empty=True)
    _validate_string_list(value.get("exclude_or_defer"), f"{path}.exclude_or_defer", errors)

    depends_on = value.get("depends_on")
    if depends_on is not None and not isinstance(depends_on, str):
        errors.append(f"{path}.depends_on: must be a string or null")
    elif isinstance(depends_on, str) and not depends_on.strip():
        errors.append(f"{path}.depends_on: must be a non-empty string or null")
    elif isinstance(depends_on, str) and depends_on.strip().casefold() not in seen_titles:
        errors.append(f"{path}.depends_on: must reference an earlier step title")

    if normalized_title is not None:
        seen_titles.add(normalized_title)

    _validate_non_empty_string(
        value.get("independently_testable_because"),
        f"{path}.independently_testable_because",
        errors,
    )
    _validate_string_list(
        value.get("suggested_checks"),
        f"{path}.suggested_checks",
        errors,
        require_non_empty=True,
    )


def validate_stackability_review(value: object) -> list[str]:
    """Return all field-addressed schema errors without modifying ``value``."""
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["review: must be an object"]

    for field in _REVIEW_FIELDS:
        if field not in value:
            errors.append(f"{field}: field is required")

    verdict = value.get("verdict")
    if not isinstance(verdict, str) or verdict not in _VERDICTS:
        errors.append(f"verdict: must be one of {', '.join(sorted(_VERDICTS))}")

    confidence = value.get("confidence")
    if not isinstance(confidence, str) or confidence not in _CONFIDENCES:
        errors.append(f"confidence: must be one of {', '.join(sorted(_CONFIDENCES))}")

    _validate_non_empty_string(value.get("rationale"), "rationale", errors)
    _validate_non_empty_string(value.get("harness_prompt"), "harness_prompt", errors)
    risks_are_list = _validate_string_list(
        value.get("risks_or_human_decisions"), "risks_or_human_decisions", errors
    )

    proposed_stack = value.get("proposed_stack")
    if not isinstance(proposed_stack, list):
        errors.append("proposed_stack: must be a list")
    else:
        seen_titles: set[str] = set()
        for index, step in enumerate(proposed_stack):
            _validate_stack_step(step, index, errors, seen_titles)

        if verdict == "split_recommended" and len(proposed_stack) < 2:
            errors.append("proposed_stack: split_recommended requires at least two steps")
        if verdict == "not_worth_splitting" and proposed_stack:
            errors.append("proposed_stack: not_worth_splitting requires no proposed steps")

    risks = value.get("risks_or_human_decisions")
    if verdict == "needs_human_context" and risks_are_list and not risks:
        errors.append(
            "risks_or_human_decisions: needs_human_context requires at least one explicit risk, "
            "decision, or question"
        )

    return errors


def new_stackability_review_record(
    reviewed_head_sha: str,
    review: object,
) -> StoredStackabilityReview:
    """Create an unpublished stackability record ready for thread metadata."""
    if not isinstance(reviewed_head_sha, str) or not reviewed_head_sha.strip():
        raise ValueError("reviewed_head_sha must be a non-empty string")
    errors = validate_stackability_review(review)
    if errors:
        raise ValueError("Invalid stackability review: " + "; ".join(errors))
    return {
        "reviewed_head_sha": reviewed_head_sha.strip(),
        "review": cast(StackabilityReview, deepcopy(review)),
        "publication": {
            "mode": None,
            "state": "unpublished",
            "github_comment_id": None,
            "github_review_id": None,
            "github_review_thread_id": None,
        },
    }


def _coerce_publication(value: object) -> StackabilityPublication | None:
    if not isinstance(value, dict):
        return None
    mode = value.get("mode")
    state = value.get("state")
    comment_id = value.get("github_comment_id")
    review_id = value.get("github_review_id")
    review_thread_id = value.get("github_review_thread_id")
    if mode is not None and mode not in _PUBLISHING_MODES:
        return None
    if state not in _PUBLICATION_STATES:
        return None
    if comment_id is not None and (not isinstance(comment_id, int) or isinstance(comment_id, bool)):
        return None
    if review_id is not None and (not isinstance(review_id, int) or isinstance(review_id, bool)):
        return None
    if review_thread_id is not None and (
        not isinstance(review_thread_id, str) or not review_thread_id
    ):
        return None
    return cast(StackabilityPublication, deepcopy(value))


def _coerce_stored_review(value: object) -> StoredStackabilityReview | None:
    if not isinstance(value, dict):
        return None
    reviewed_head_sha = value.get("reviewed_head_sha")
    review = value.get("review")
    publication = _coerce_publication(value.get("publication"))
    if not isinstance(reviewed_head_sha, str) or not reviewed_head_sha:
        return None
    if validate_stackability_review(review) or publication is None:
        return None
    return {
        "reviewed_head_sha": reviewed_head_sha,
        "review": cast(StackabilityReview, deepcopy(review)),
        "publication": publication,
    }


async def get_stackability_review(thread_id: str) -> StoredStackabilityReview | None:
    """Fetch the latest valid stackability record from reviewer metadata."""
    metadata = await get_thread_metadata(thread_id)
    return _coerce_stored_review(metadata.get(_STACKABILITY_METADATA_KEY))


async def set_stackability_review(thread_id: str, record: object) -> None:
    """Persist the latest stackability record separately from bug findings."""
    coerced = _coerce_stored_review(record)
    if coerced is None:
        raise ValueError("Invalid stored stackability review")
    await set_reviewer_thread_metadata(
        thread_id,
        extra={_STACKABILITY_METADATA_KEY: coerced},
    )


async def update_stackability_review(
    thread_id: str,
    *,
    reviewed_head_sha: str | None = None,
    review: object | None = None,
    publication: object | None = None,
) -> StoredStackabilityReview | None:
    """Replace selected fields on the latest record and persist the result."""
    current = await get_stackability_review(thread_id)
    if current is None:
        return None
    candidate: dict[str, Any] = deepcopy(dict(current))
    if reviewed_head_sha is not None:
        candidate["reviewed_head_sha"] = reviewed_head_sha
    if review is not None:
        candidate["review"] = review
    if publication is not None:
        candidate["publication"] = publication
    coerced = _coerce_stored_review(candidate)
    if coerced is None:
        raise ValueError("Invalid stackability review update")
    await set_stackability_review(thread_id, coerced)
    return coerced


_VERDICT_LABELS: dict[str, str] = {
    "split_recommended": "Split recommended",
    "not_worth_splitting": "Not worth splitting",
    "needs_human_context": "Human context needed",
}


def _render_string_list(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values) if values else "- None"


def _render_review_details(record: StoredStackabilityReview) -> str:
    review = record["review"]
    parts = [
        f"**Verdict:** {_VERDICT_LABELS[review['verdict']]}",
        f"**Confidence:** {review['confidence'].capitalize()}",
        "",
        review["rationale"].strip(),
    ]
    if review["proposed_stack"]:
        parts.extend(["", "### Proposed stack"])
        for index, step in enumerate(review["proposed_stack"], start=1):
            dependency = step["depends_on"] or "None"
            parts.extend(
                [
                    "",
                    f"#### {index}. {step['title']}",
                    "",
                    step["purpose"],
                    "",
                    "**Include**",
                    _render_string_list(step["include"]),
                    "",
                    "**Exclude or defer**",
                    _render_string_list(step["exclude_or_defer"]),
                    "",
                    f"**Depends on:** {dependency}",
                    f"**Independently testable because:** {step['independently_testable_because']}",
                    "",
                    "**Suggested checks**",
                    _render_string_list(step["suggested_checks"]),
                ]
            )
    if review["risks_or_human_decisions"]:
        parts.extend(
            [
                "",
                "### Risks, decisions, or questions",
                _render_string_list(review["risks_or_human_decisions"]),
            ]
        )
    parts.extend(
        [
            "",
            "### Restacking handoff prompt",
            "",
            "```text",
            review["harness_prompt"].strip(),
            "```",
        ]
    )
    return "\n".join(parts)


def render_stackability_advisory(record: StoredStackabilityReview) -> str:
    """Render a non-blocking GitHub issue-comment advisory."""
    return "\n".join(
        [
            STACKABILITY_REVIEW_MARKER,
            "",
            "## Open SWE Stackability Review (non-blocking)",
            "",
            _render_review_details(record),
        ]
    )


def render_stackability_blocking_body(record: StoredStackabilityReview) -> str:
    """Render the body for an opt-in resolvable GitHub review thread."""
    verdict = record["review"]["verdict"]
    disposition = "No split recommended" if verdict == "not_worth_splitting" else "Action required"
    return "\n".join(
        [
            STACKABILITY_REVIEW_MARKER,
            "",
            f"## Open SWE Stackability Review — {disposition}",
            "",
            _render_review_details(record),
        ]
    )


def render_stackability_dashboard_markdown(record: StoredStackabilityReview) -> str:
    """Render dashboard-friendly Markdown without GitHub synchronization metadata."""
    return "\n".join(
        [
            "## Stackability review",
            "",
            f"Reviewed head: `{record['reviewed_head_sha']}`",
            "",
            _render_review_details(record),
        ]
    )
