"""Move review settings, assessment feedback, and baby-sit watches out of the LangGraph Store.

Runs through :func:`openswe.database.store_imports.run_store_import`, which
stops running it once a pass finds nothing left; a later release can delete
this module. Published assessments drain separately, in the background
(:func:`openswe.review.assessment_feedback.run_assessment_import`). Slack
review-notice deduplication is not moved: a notice redelivered after the
cutover posts once more at most.
"""

from openswe.baby_sit import WATCHES
from openswe.database.store_imports import StoreImport
from openswe.review.assessment_feedback import import_store_feedback
from openswe.review.enabled_repos import import_store_enabled_review_repos
from openswe.review.styles import REVIEW_STYLES


async def import_review_records() -> StoreImport:
    return (
        await import_store_enabled_review_repos()
        + await REVIEW_STYLES.import_store()
        + await import_store_feedback()
        + await WATCHES.import_store()
    )
