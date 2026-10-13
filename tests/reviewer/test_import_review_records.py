import pytest

from openswe.baby_sit import WATCHES, BabySitWatch
from openswe.database.store_imports import StoreImport
from openswe.review.assessment_feedback import PublishedAssessment, import_store_assessments
from openswe.review.enabled_repos import list_enabled_review_repos, set_review_repo_enabled
from openswe.review.import_records import import_review_records
from openswe.review.styles import REVIEW_STYLES, ReviewStylePromptUpdate
from tests.conftest import FakeStore

pytestmark = pytest.mark.usefixtures("registry_db")


async def test_import_moves_review_records_and_keeps_decisions_made_in_postgres(
    fake_store: FakeStore,
) -> None:
    await set_review_repo_enabled("acme/off", False)
    await REVIEW_STYLES.update_prompts("acme/kept", ReviewStylePromptUpdate(custom_prompt="PG"))
    fake_store.seed(["enabled_review_repos"], "default", {"repos": ["Acme/One", "acme/off"]})
    fake_store.seed(
        ["review_styles"],
        "Acme/One",
        {
            "full_name": "Acme/One",
            "custom_prompt": "Be terse",
            "approval_mode": "approve",
            "created_by": "ada",
            "created_at": "2026-01-02T03:04:05+00:00",
        },
    )
    fake_store.seed(
        ["review_styles"], "acme/kept", {"full_name": "acme/kept", "custom_prompt": "S"}
    )
    watch = BabySitWatch(
        key="acme/one#7", thread_id="t1", owner="acme", repo="one", pr_number=7, head_sha="h"
    )
    fake_store.seed(["baby_sit_watches"], watch.key, watch.model_dump(mode="json"))

    assert await import_review_records() == StoreImport(moved=5)

    assert await list_enabled_review_repos() == ["Acme/One"]
    imported = await REVIEW_STYLES.get("acme/one")
    assert imported is not None
    assert (imported.custom_prompt, imported.approval_mode, imported.created_by) == (
        "Be terse",
        "approve",
        "ada",
    )
    assert imported.created_at.startswith("2026-01-02T03:04:05")
    kept = await REVIEW_STYLES.get("acme/kept")
    assert kept is not None and kept.custom_prompt == "PG"
    [imported_watch] = await WATCHES.list_active(owner="Acme", repo="one")
    assert (imported_watch.key, imported_watch.thread_id, imported_watch.head_sha) == (
        "acme/one#7",
        "t1",
        "h",
    )
    assert await imported_watch.belongs_to("t1")
    assert not any(fake_store.items.values())


async def test_assessments_drain_from_the_store(fake_store: FakeStore) -> None:
    for review_id in (1, 2):
        fake_store.seed(
            ["review_assessments"],
            str(review_id),
            PublishedAssessment(
                review_id=review_id,
                owner="o",
                repo="r",
                pr_number=1,
                head_sha="a" * 40,
                risk_score=1,
                decision="would_approve",
                explanation="Docs only.",
            ).model_dump(mode="json"),
        )

    assert await import_store_assessments() == StoreImport(moved=2)

    assessment = await PublishedAssessment.get(2)
    assert assessment is not None and assessment.review_id == 2
    assert not fake_store.values(["review_assessments"])
