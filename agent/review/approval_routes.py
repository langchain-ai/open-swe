"""Authorized settings API for review approval policies."""

from fastapi import APIRouter, Depends, HTTPException

from agent.dashboard.deps import SESSION_DEP, session_is_admin
from agent.dashboard.oauth import require_same_origin_for_mutations
from agent.dashboard.repo_access import require_repo_access_for_user
from agent.review.approval_settings import (
    PolicyConflict,
    PolicySettingsUpdate,
    PolicySettingsView,
    get_policy_settings,
    normalize_repository,
    save_policy_settings,
)

router = APIRouter()


async def _authorize(session: dict[str, object], repository: str | None) -> tuple[str, str | None]:
    login = session.get("sub")
    if not isinstance(login, str) or not login:
        raise HTTPException(401, "Login required")
    try:
        repository = normalize_repository(repository)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if repository:
        await require_repo_access_for_user(login, repository)
    return login, repository


@router.get("/review-approval-policy")
async def read_approval_policy(
    repository: str | None = None,
    session: dict[str, object] = SESSION_DEP,
) -> PolicySettingsView:
    _, repository = await _authorize(session, repository)
    view = await get_policy_settings(repository)
    return view.model_copy(update={"can_edit": session_is_admin(session)})


@router.put("/review-approval-policy", dependencies=[Depends(require_same_origin_for_mutations)])
async def update_approval_policy(
    body: PolicySettingsUpdate,
    repository: str | None = None,
    session: dict[str, object] = SESSION_DEP,
) -> PolicySettingsView:
    if not session_is_admin(session):
        raise HTTPException(403, "Only Open SWE admins can edit approval policy")
    login, repository = await _authorize(session, repository)
    try:
        view = await save_policy_settings(
            repository, policy=body.policy, expected_version=body.expected_version, login=login
        )
    except PolicyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return view.model_copy(update={"can_edit": True})
