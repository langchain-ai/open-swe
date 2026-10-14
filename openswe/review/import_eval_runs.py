"""Move the reviewer eval record from the LangGraph Store into ``reviewer_eval_run``.

Runs through :func:`openswe.database.store_imports.run_store_import`, which
stops running it once a pass finds nothing left; a later release can delete
this module.

The Store held one record, the latest eval. It is copied into a row of its own,
and deleted only once the copy has been in place for a day: replicas of the
previous release show and update it until their rollout finishes. A record
saved after the copy, by such a replica or an eval sandbox it launched,
replaces the copy.
"""

import logging
from datetime import UTC, datetime

from pydantic import ValidationError

from openswe.database.store_imports import (
    StoreImport,
    may_delete_store_copy,
    store_copy_is_newer,
    timestamp,
)
from openswe.review.eval_jobs import (
    EVAL_CONFIG,
    ReviewerEvalRun,
    ReviewerEvalStatus,
    resolve_eval_config,
)
from openswe.store import delete_value, get_value
from openswe.utils.json_types import JsonObject, as_json_object

logger = logging.getLogger(__name__)

STORE_NAMESPACE: list[str] = ["evals"]
STORE_KEY = "reviewer"


async def import_store_reviewer_eval() -> StoreImport:
    value = await get_value(STORE_NAMESPACE, STORE_KEY)
    if value is None:
        return StoreImport()
    copy = await ReviewerEvalRun.latest(copied_from_store=True)
    if copy is None or store_copy_is_newer(value, copy.updated_at):
        try:
            run = _run_from_store(value)
        except ValidationError:
            logger.warning("Leaving an unreadable reviewer eval in the Store", exc_info=True)
            return StoreImport()
        if run is None:
            return StoreImport()
        launcher = value.get("created_by")
        await run.put_store_copy(
            None if copy is None else copy.id, launcher if isinstance(launcher, str) else None
        )
        return StoreImport(moved=1)
    if not may_delete_store_copy(copy.updated_at):
        return StoreImport(waiting=1)
    await delete_value(STORE_NAMESPACE, STORE_KEY)
    return StoreImport(moved=1)


def _run_from_store(value: JsonObject) -> ReviewerEvalRun | None:
    """The run a Store record describes; ``None`` for one that never ran."""
    status = ReviewerEvalStatus.model_validate(
        {
            **value,
            "config_snapshot": {
                **resolve_eval_config(),
                **as_json_object(value.get("config_snapshot")),
            },
        }
    )
    if status.status == "idle":
        return None
    progress = status.progress
    return ReviewerEvalRun(
        status=status.status,
        config=dict(EVAL_CONFIG.validate_python(status.config_snapshot)),
        created_at=timestamp(value.get("started_at")) or datetime.now(UTC),
        heartbeat_at=timestamp(value.get("heartbeat")),
        example_limit=status.limit,
        sandbox_name=status.worker_id,
        experiment_url=status.experiment_url,
        error=status.error,
        log_tail=status.log_tail,
        completed_examples=None if progress is None else progress.completed,
        total_examples=None if progress is None else progress.total,
        finished_at=timestamp(value.get("finished_at")),
        copied_from_store=True,
    )
