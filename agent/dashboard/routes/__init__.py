"""The dashboard API, composed from one router per resource the UI sees.

Path matching runs in registration order, so each module keeps its own routes'
relative order (``/threads/sidebar`` before ``/threads/{thread_id}``). Across
modules the first path segment differs, so the order below is only for reading.
"""

from fastapi import APIRouter, Depends

from ..oauth import require_same_origin_for_mutations
from . import (
    admin,
    agent_instructions,
    auth,
    credentials,
    environments,
    profile,
    repo_snapshots,
    repos,
    review_chat,
    review_styles,
    reviews,
    schedules,
    skills,
    team,
    terminal,
    threads,
    voice,
)

__all__ = ["router"]

router = APIRouter(
    prefix="/dashboard/api",
    tags=["dashboard"],
    dependencies=[Depends(require_same_origin_for_mutations)],
)

router.include_router(auth.router)
router.include_router(profile.router)
router.include_router(credentials.router)
router.include_router(team.router)
router.include_router(admin.router)
router.include_router(repo_snapshots.router)
router.include_router(environments.router)
router.include_router(repos.router)
router.include_router(reviews.router)
router.include_router(review_chat.router)
router.include_router(review_styles.router)
router.include_router(agent_instructions.router)
router.include_router(skills.router)
router.include_router(schedules.router)
router.include_router(threads.router)
router.include_router(terminal.router)
router.include_router(voice.router)
