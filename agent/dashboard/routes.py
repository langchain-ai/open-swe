"""Aggregate router mounting every dashboard API under ``/dashboard/api``."""

from fastapi import APIRouter, Depends

from agent.analytics.routes import router as analytics_router
from agent.api_keys.routes import router as api_keys_router
from agent.bridge.routes import router as bridge_router
from agent.dashboard.agent_instructions import router as agent_instructions_router
from agent.dashboard.auth_routes import router as auth_router
from agent.dashboard.notion_routes import router as notion_router
from agent.dashboard.oauth import require_same_origin_for_mutations
from agent.dashboard.options_routes import router as options_router
from agent.dashboard.profiles import router as profiles_router
from agent.dashboard.user_instructions import router as user_instructions_router
from agent.dashboard.user_preferences import router as user_preferences_router
from agent.dashboard.workspace_settings import router as workspace_settings_router
from agent.github.dashboard_routes import router as repos_router
from agent.github.pull_request_dashboard_routes import router as pull_requests_router
from agent.incidents.document_routes import router as incident_documents_router
from agent.incidents.routes import router as incidents_router
from agent.mcp.routes import router as mcp_router
from agent.review.conversation import router as review_conversation_router
from agent.review.routes import router as review_router
from agent.schedules.routes import router as schedules_router
from agent.skill_store.routes import router as skills_router
from agent.slack.dashboard_routes import router as slack_router
from agent.threads.routes import router as threads_router
from agent.transcript.routes import router as transcript_router
from agent.users.routes import router as users_router
from agent.workspaces.routes import router as workspaces_router

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
router.include_router(users_router)
router.include_router(notion_router)
router.include_router(slack_router)
router.include_router(workspace_settings_router)
router.include_router(mcp_router)
router.include_router(workspaces_router)
router.include_router(repos_router)
router.include_router(pull_requests_router)
router.include_router(review_router)
router.include_router(review_conversation_router)
router.include_router(agent_instructions_router)
router.include_router(skills_router)
router.include_router(analytics_router)
router.include_router(schedules_router)
router.include_router(threads_router)
router.include_router(transcript_router)
router.include_router(api_keys_router)
router.include_router(bridge_router)
