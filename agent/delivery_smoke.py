"""End-to-end smoke orchestration for one Sports CMS delivery item."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from . import delivery_merge, delivery_results, delivery_review, delivery_runner
from .delivery_queue import (
    list_delivery_queue_items,
    read_delivery_queue_item,
    transition_delivery_queue_status,
)
from .linear_queue import LinearIssueClient, linear_policy_from_project, poll_linear_delivery_queue
from .project_registry import (
    default_sports_cms_delivery_project,
    sports_cms_ddev_sandbox_profile,
    upsert_delivery_project,
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _merge_project(
    project: Mapping[str, Any],
    overrides: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(project)
    for key, value in dict(overrides or {}).items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = {**dict(current), **dict(value)}
        else:
            merged[key] = value
    return merged


def _sports_cms_sandbox_profile_from_env(env: Mapping[str, str]) -> dict[str, Any] | None:
    project_path = _string(
        env.get("SPORTS_CMS_DDEV_PROJECT_PATH") or env.get("SPORTS_CMS_PROJECT_PATH")
    )
    preview_url = _string(env.get("SPORTS_CMS_PREVIEW_URL"))
    if not project_path or not preview_url:
        return None
    return sports_cms_ddev_sandbox_profile(
        project_path=project_path,
        preview_url=preview_url,
        theme_path=_string(env.get("SPORTS_CMS_THEME_PATH")) or "web/themes/custom/adesso_theme",
        artifact_dir=_string(env.get("SPORTS_CMS_ARTIFACT_DIR"))
        or "/tmp/open-swe-sports-cms-artifacts",
        sdc_component_id=_string(env.get("SPORTS_CMS_SDC_COMPONENT_ID")) or None,
    )


async def _select_queue_item(
    project_id: str,
    *,
    external_work_item_id: str | None,
) -> dict[str, Any] | None:
    records = await list_delivery_queue_items({"project_id": project_id})
    scoped = [
        record
        for record in records
        if not external_work_item_id or record.get("external_work_item_id") == external_work_item_id
    ]
    for record in scoped:
        if record.get("status") == "queued":
            return record
    return scoped[0] if scoped else None


def _worker_branch_created(item: Mapping[str, Any], worker_result: Mapping[str, Any]) -> bool:
    if worker_result.get("branch_created") is True:
        return True
    worktree = _mapping(item.get("worktree"))
    return bool(_string(item.get("branch")) and _string(worktree.get("branch")))


def _draft_pr_created(worker_result: Mapping[str, Any]) -> bool:
    if worker_result.get("draft_pull_request_created") is True:
        return True
    pr = _mapping(worker_result.get("pr"))
    return pr.get("created_as_draft") is True or pr.get("draft") is True


def _drupal_sandbox_preview(
    item: Mapping[str, Any],
    worker_result: Mapping[str, Any],
) -> bool:
    sandbox = _mapping(worker_result.get("sandbox_evidence"))
    qa_evidence = _mapping(item.get("qa_evidence"))
    preview_url = _string(sandbox.get("preview_url") or qa_evidence.get("preview_url"))
    provider_text = " ".join(
        _string(sandbox.get(key)).lower() for key in ("provider", "profile", "kind")
    )
    return bool(preview_url and sandbox.get("server_side") is True and "drupal" in provider_text)


def _qa_evidence_complete(item: Mapping[str, Any]) -> bool:
    evidence = _mapping(item.get("qa_evidence"))
    return bool(
        evidence.get("complete") is True
        and _string(evidence.get("preview_url"))
        and _list(evidence.get("screenshots"))
        and (_list(evidence.get("videos")) or _list(evidence.get("traces")))
        and _list(evidence.get("gates"))
    )


def _independent_review_and_qa(item: Mapping[str, Any]) -> bool:
    review_result = _mapping(item.get("review_result"))
    qa_result = _mapping(review_result.get("qa_result"))
    worker_thread_id = _string(item.get("worker_thread_id"))
    reviewer_thread_id = _string(item.get("reviewer_thread_id"))
    qa_thread_id = _string(item.get("qa_thread_id"))
    return bool(
        review_result.get("status") == "passed"
        and reviewer_thread_id
        and qa_thread_id
        and reviewer_thread_id != worker_thread_id
        and qa_thread_id != worker_thread_id
        and qa_result.get("passed") is True
    )


def _auto_merge_complete(item: Mapping[str, Any]) -> bool:
    return item.get("status") == "done" and item.get("merge_status") == "merged"


def _acceptance(
    *,
    item: Mapping[str, Any],
    worker_result: Mapping[str, Any],
    linear_issue_queued: bool,
) -> dict[str, bool]:
    return {
        "linear_issue_queued": linear_issue_queued,
        "worker_branch_created": _worker_branch_created(item, worker_result),
        "draft_pull_request_created": _draft_pr_created(worker_result),
        "drupal_sandbox_preview": _drupal_sandbox_preview(item, worker_result),
        "qa_evidence_complete": _qa_evidence_complete(item),
        "independent_review_and_qa": _independent_review_and_qa(item),
        "agent_reviewed_auto_merge": _auto_merge_complete(item),
    }


_BLOCKER_BY_ACCEPTANCE = {
    "linear_issue_queued": "linear_issue_not_queued",
    "worker_branch_created": "worker_branch_missing",
    "draft_pull_request_created": "draft_pull_request_missing",
    "drupal_sandbox_preview": "drupal_sandbox_preview_missing",
    "qa_evidence_complete": "qa_evidence_incomplete",
    "independent_review_and_qa": "independent_review_or_qa_missing",
    "agent_reviewed_auto_merge": "auto_merge_incomplete",
}


async def _block_smoke(
    item_id: str,
    *,
    reason: str,
    acceptance: Mapping[str, bool],
    proof: Mapping[str, Any],
) -> dict[str, Any]:
    blocker = {"code": reason, "message": reason}
    updated = await transition_delivery_queue_status(
        item_id,
        "blocked",
        reason=reason,
        extra={
            "blocker_reason": reason,
            "blockers": [blocker],
            "smoke_proof": dict(proof),
            "delivery": {
                **_mapping((await read_delivery_queue_item(item_id) or {}).get("delivery")),
                "queue_status": "blocked",
                "blocker_reason": reason,
            },
        },
    )
    return {
        "status": "blocked",
        "reason": reason,
        "item_id": item_id,
        "acceptance": dict(acceptance),
        "proof": {**dict(proof), "final_status": updated.get("status")},
    }


def _first_failed_acceptance(acceptance: Mapping[str, bool]) -> str | None:
    for key, passed in acceptance.items():
        if not passed:
            return _BLOCKER_BY_ACCEPTANCE[key]
    return None


async def run_sports_cms_delivery_smoke(
    *,
    tracker_config: Mapping[str, Any],
    vcs_config: Mapping[str, Any],
    linear_client: LinearIssueClient,
    worker_result: Mapping[str, Any],
    review_result: Mapping[str, Any],
    merge_token: str | None,
    client: Any | None = None,
    merge_func: delivery_merge.MergeExecutor | None = None,
    project_overrides: Mapping[str, Any] | None = None,
    external_work_item_id: str | None = None,
    start_checks: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one Sports CMS item through the V1 delivery smoke path."""
    project = _merge_project(
        default_sports_cms_delivery_project(
            tracker_config=tracker_config,
            vcs_config=vcs_config,
            sandbox_profile=_sports_cms_sandbox_profile_from_env(os.environ),
        ),
        project_overrides,
    )
    stored_project = await upsert_delivery_project(project)
    poll_result = await poll_linear_delivery_queue(
        linear_policy_from_project(stored_project),
        client=linear_client,
    )
    item = await _select_queue_item(
        str(stored_project["project_id"]),
        external_work_item_id=external_work_item_id,
    )
    proof: dict[str, Any] = {
        "project_id": stored_project["project_id"],
        "poll": poll_result,
    }
    if item is None:
        return {
            "status": "blocked",
            "reason": "linear_issue_not_queued",
            "acceptance": dict.fromkeys(
                (
                    "linear_issue_queued",
                    "worker_branch_created",
                    "draft_pull_request_created",
                    "drupal_sandbox_preview",
                    "qa_evidence_complete",
                    "independent_review_and_qa",
                    "agent_reviewed_auto_merge",
                ),
                False,
            ),
            "proof": proof,
        }

    item_id = item["id"]
    linear_issue_queued = item.get("status") == "queued"
    proof["item_id"] = item_id
    proof["queued_status"] = item.get("status")
    if not linear_issue_queued:
        acceptance = _acceptance(
            item=item,
            worker_result=worker_result,
            linear_issue_queued=False,
        )
        return await _block_smoke(
            item_id,
            reason="linear_issue_not_queued",
            acceptance=acceptance,
            proof=proof,
        )

    smoke_start_checks = {
        "github_credentials": bool(merge_token),
        "ai_hub_ready": True,
        "budget_available": True,
        **dict(start_checks or {}),
    }
    worker_launch = await delivery_runner.launch_delivery_worker(
        item_id,
        client=client,
        start_checks=smoke_start_checks,
    )
    proof["worker_launch"] = worker_launch
    if worker_launch.get("status") != "launched":
        current = await read_delivery_queue_item(item_id) or item
        acceptance = _acceptance(
            item=current,
            worker_result=worker_result,
            linear_issue_queued=True,
        )
        return await _block_smoke(
            item_id,
            reason=str(worker_launch.get("reason") or "worker_launch_failed"),
            acceptance=acceptance,
            proof=proof,
        )

    ingested = await delivery_results.ingest_delivery_worker_result(item_id, worker_result)
    proof["worker_result"] = {
        "status": ingested.get("status"),
        "reason": ingested.get("status_reason"),
        "gate_rollup": ingested.get("gate_rollup"),
    }
    if ingested.get("status") != "review":
        acceptance = _acceptance(
            item=ingested,
            worker_result=worker_result,
            linear_issue_queued=True,
        )
        return await _block_smoke(
            item_id,
            reason=str(ingested.get("blocker_reason") or "worker_result_blocked"),
            acceptance=acceptance,
            proof=proof,
        )

    review_launch = await delivery_review.launch_delivery_review_checks(item_id, client=client)
    proof["review_launch"] = review_launch
    if review_launch.get("status") != "launched":
        current = await read_delivery_queue_item(item_id) or ingested
        acceptance = _acceptance(
            item=current,
            worker_result=worker_result,
            linear_issue_queued=True,
        )
        return await _block_smoke(
            item_id,
            reason=str(review_launch.get("reason") or "review_launch_failed"),
            acceptance=acceptance,
            proof=proof,
        )

    reviewed = await delivery_review.record_delivery_review_result(item_id, review_result)
    proof["review_result"] = {
        "status": _mapping(reviewed.get("review_result")).get("status"),
        "reviewed_sha": reviewed.get("reviewed_sha"),
        "merge_eligible": reviewed.get("merge_eligible"),
    }
    acceptance_before_merge = _acceptance(
        item=reviewed,
        worker_result=worker_result,
        linear_issue_queued=True,
    )
    pre_merge_blocker = _first_failed_acceptance(
        {
            key: value
            for key, value in acceptance_before_merge.items()
            if key != "agent_reviewed_auto_merge"
        }
    )
    if pre_merge_blocker:
        return await _block_smoke(
            item_id,
            reason=pre_merge_blocker,
            acceptance=acceptance_before_merge,
            proof=proof,
        )

    merged = await delivery_merge.execute_delivery_merge(
        item_id,
        token=merge_token,
        merge_func=merge_func,
    )
    proof["merge_result"] = {
        "status": merged.get("status"),
        "reason": merged.get("status_reason"),
        "merge_status": merged.get("merge_status"),
        "merge_commit_sha": merged.get("merge_commit_sha"),
    }
    acceptance = _acceptance(
        item=merged,
        worker_result=worker_result,
        linear_issue_queued=True,
    )
    blocker = _first_failed_acceptance(acceptance)
    if blocker:
        return {
            "status": "blocked",
            "reason": str(merged.get("blocker_reason") or merged.get("status_reason") or blocker),
            "item_id": item_id,
            "acceptance": acceptance,
            "proof": {**proof, "final_status": merged.get("status")},
        }
    return {
        "status": "passed",
        "item_id": item_id,
        "acceptance": acceptance,
        "proof": {**proof, "final_status": merged.get("status")},
    }
