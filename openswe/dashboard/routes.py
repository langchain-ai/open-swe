"""Aggregate router mounting every dashboard API under ``/dashboard/api``."""

from fastapi import APIRouter, Depends

from openswe.analytics.routes import router as analytics_router
from openswe.api_keys.routes import router as api_keys_router
from openswe.audit_logs.routes import router as audit_logs_router
from openswe.bridge.routes import router as bridge_router
from openswe.dashboard.agent_instructions import router as agent_instructions_router
from openswe.dashboard.auth_routes import router as auth_router
from openswe.dashboard.client_errors import router as client_errors_router
from openswe.dashboard.langsmith_routes import router as langsmith_router
from openswe.dashboard.oauth import require_same_origin_for_mutations
from openswe.dashboard.options_routes import router as options_router
from openswe.dashboard.profiles import router as profiles_router
from openswe.dashboard.user_instructions import router as user_instructions_router
from openswe.dashboard.user_preferences import router as user_preferences_router
from openswe.dashboard.workspace_settings import router as workspace_settings_router
from openswe.github.dashboard_routes import router as repos_router
from openswe.github.pull_request_dashboard_routes import router as pull_requests_router
from openswe.human_review.routes import router as human_review_router
from openswe.inbox.routes import router as inbox_router
from openswe.incidents.document_routes import router as incident_documents_router
from openswe.incidents.routes import router as incidents_router
from openswe.mcp.routes import router as mcp_router
from openswe.review.conversation import router as review_conversation_router
from openswe.review.routes import router as review_router
from openswe.schedules.routes import router as schedules_router
from openswe.skill_store.routes import router as skills_router
from openswe.slack.dashboard_routes import router as slack_router
from openswe.threads.routes import router as threads_router
from openswe.transcript.routes import router as transcript_router
from openswe.ui_invalidations.routes import router as ui_invalidations_router
from openswe.users.routes import router as users_router
from openswe.workspaces.routes import router as workspaces_router

router = APIRouter(
    prefix="/dashboard/api",
    tags=["dashboard"],
    dependencies=[Depends(require_same_origin_for_mutations)],
)
router.include_router(incidents_router)
router.include_router(incident_documents_router, prefix="/incidents/documents")
router.include_router(auth_router)
router.include_router(client_errors_router)
router.include_router(user_instructions_router)
router.include_router(user_preferences_router)
router.include_router(options_router)
router.include_router(profiles_router)
router.include_router(users_router)
router.include_router(langsmith_router)
router.include_router(slack_router)
router.include_router(workspace_settings_router)
router.include_router(mcp_router)
router.include_router(workspaces_router)
router.include_router(repos_router)
router.include_router(pull_requests_router)
router.include_router(human_review_router)
router.include_router(review_router)
router.include_router(review_conversation_router)
router.include_router(agent_instructions_router)
router.include_router(skills_router)
router.include_router(analytics_router)
router.include_router(audit_logs_router)
router.include_router(schedules_router)
router.include_router(threads_router)
router.include_router(inbox_router)
router.include_router(transcript_router)
router.include_router(api_keys_router)
router.include_router(bridge_router)
router.include_router(ui_invalidations_router)
