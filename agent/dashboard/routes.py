"""Aggregate router mounting every dashboard API under ``/dashboard/api``."""

from fastapi import APIRouter, Depends

from agent.analytics.api import router as analytics_router
from agent.dashboard.agent_instructions import router as agent_instructions_router
from agent.dashboard.auth_api import router as auth_router
from agent.dashboard.notion_api import router as notion_router
from agent.dashboard.oauth import require_same_origin_for_mutations
from agent.dashboard.options_api import router as options_router
from agent.dashboard.profiles import router as profiles_router
from agent.dashboard.team_settings import router as team_settings_router
from agent.dashboard.user_instructions import router as user_instructions_router
from agent.dashboard.user_mappings import router as user_mappings_router
from agent.dashboard.user_preferences import router as user_preferences_router
from agent.environments.api import router as environments_router
from agent.github.repos import router as repos_router
from agent.incidents.api import router as incidents_router
from agent.incidents.document_api import router as incident_documents_router
from agent.mcp.api import router as mcp_router
from agent.review.api import router as review_router
from agent.schedules.api import router as schedules_router
from agent.skill_store.api import router as skills_router
from agent.slack.api import router as slack_router
from agent.threads.routes import router as threads_router

router = APIRouter(
    prefix="/dashboard/api",
    tags=["dashboard"],
    dependencies=[Depends(require_same_origin_for_mutations)],
)
router.include_router(incidents_router)
router.include_router(incident_documents_router, prefix="/incidents/documents")
router.include_router(auth_router)
router.include_router(user_instructions_router)
router.include_router(user_preferences_router)
router.include_router(options_router)
router.include_router(profiles_router)
router.include_router(user_mappings_router)
router.include_router(notion_router)
router.include_router(slack_router)
router.include_router(team_settings_router)
router.include_router(mcp_router)
router.include_router(environments_router)
router.include_router(repos_router)
router.include_router(review_router)
router.include_router(agent_instructions_router)
router.include_router(skills_router)
router.include_router(analytics_router)
router.include_router(schedules_router)
router.include_router(threads_router)
