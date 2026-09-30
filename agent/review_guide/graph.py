"""Review guide graph.

Walks one person through a pull request in a Slack code channel, a small chunk
at a time: the guide picks each chunk as line ranges as the conversation goes,
and the server shows it and records it once the reader says it looks good.
Every run first pins the checkout to the PR head; a newer head restarts the
walkthrough, and lines the reader already approved never come back.
"""

import logging
from typing import Any, cast

from deepagents import create_deep_agent
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel
from langgraph.runtime import Runtime

from agent.dashboard.options import gate_fable_model
from agent.dashboard.workspace_settings_cache import cached_workspace_settings
from agent.github.app import get_github_app_installation_token_with_expiry
from agent.github.thread_token import cache_github_token_for_thread
from agent.middleware import (
    BasePrepareRunMiddleware,
    ModelCallTimeoutMiddleware,
    ModelErrorMiddleware,
    RepairOrphanedToolCallsMiddleware,
    SanitizeFireworksMessagesMiddleware,
    SanitizeOpenAIResponsesMiddleware,
    SanitizeThinkingBlocksMiddleware,
    SanitizeToolInputsMiddleware,
    StableToolResultOrderMiddleware,
    TimeoutWrapupMiddleware,
    ToolErrorMiddleware,
)
from agent.middleware.prepare_run import PrepareRunState
from agent.prompts import apply_tool_descriptions, prompt
from agent.review.walkthrough import Walkthrough
from agent.review_guide import git
from agent.review_guide.advance import approve_click
from agent.review_guide.context import guide_repo_dir
from agent.review_guide.diff import parse
from agent.review_guide.github import fetch_head
from agent.review_guide.messages import resume, retire
from agent.review_guide.sessions import ReviewGuideSession
from agent.review_guide.walk import Walk
from agent.run_config import RunConfig
from agent.runtime import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_RECURSION_LIMIT,
    bindable_config,
    ensure_sandbox_for_thread,
    get_cached_sandbox_backend,
    graph_loaded_for_execution,
)
from agent.sandboxes.repo_prep import prepare_review_repo
from agent.slack.code_channels import repo_context_bar_items, set_context_bar, set_view
from agent.slack.tools.reply import slack_reply
from agent.tools.approve_pull_request import approve_pull_request
from agent.tools.code_channel_set_view import code_channel_set_view
from agent.tools.mark_pull_request_ready import mark_pull_request_ready
from agent.tools.record_author_feedback import record_author_feedback
from agent.tools.review_walkthrough import (
    edit_queue,
    end_walkthrough,
    move_to_other,
    queue_chunk,
    show_chunk,
    show_other,
    show_queued,
    skip_changes,
)
from agent.utils.dashboard_links import dashboard_thread_url
from agent.utils.deferred_model import make_deferred_error_model
from agent.utils.model import DEFAULT_LLM_REASONING, make_model, provider_model_kwargs

logger = logging.getLogger(__name__)

GUIDE_MODEL_CALL_LIMIT = 80


async def _ensure_guide_sandbox(
    thread_id: str, owner: str, repo: str, workspace_slug: str | None
) -> SandboxBackendProtocol:
    token, expires_at = await get_github_app_installation_token_with_expiry(repositories=[repo])
    if not token:
        raise RuntimeError(f"GitHub App installation token unavailable for guide {thread_id}")
    cache_github_token_for_thread(
        thread_id, token, expires_at=expires_at, is_bot_token=True, repositories=[repo]
    )
    return await ensure_sandbox_for_thread(
        thread_id,
        workspace_slug=workspace_slug,
        github_proxy_repositories=[f"{owner}/{repo}"],
        allow_replacement=True,
    )


class PrepareReviewGuideRunMiddleware(BasePrepareRunMiddleware):
    def __init__(self, *, thread_id: str, config: RunnableConfig) -> None:
        self._thread_id = thread_id
        self._config = config

    def _prepare_config_fingerprint(self) -> object:
        return {"invocation_id": RunConfig.from_config(self._config).invocation_id}

    async def _prepare(self, state: PrepareRunState, runtime: Runtime) -> dict[str, Any]:  # noqa: ARG002
        session = await ReviewGuideSession.get(self._thread_id)
        if session is None:
            raise RuntimeError("review guide run has no session")
        pr = session.pull_request
        head = await fetch_head(pr.owner, pr.repo, pr.number)
        if head is None:
            raise RuntimeError("review guide could not read the pull request")
        backend = await _ensure_guide_sandbox(
            self._thread_id, pr.owner, pr.repo, session.workspace_slug
        )
        repo_dir = await guide_repo_dir(backend, pr.repo)
        work_dir = repo_dir.rsplit("/", 1)[0]
        author = session.mode == "author"
        updates: dict[str, Any] = {
            "work_dir": work_dir,
            "rendered_system_prompt": prompt(
                "review-guide/main",
                pr_number=pr.number,
                repo_full_name=pr.repo_full_name,
                repo_dir=repo_dir,
                author=author,
                draft=head.draft,
                human_input=(await Walkthrough.human_input_for(pr.id) or "") if author else "",
            ),
        }
        if await git.built_for(backend, repo_dir) != (head.base.sha, head.head.sha):
            ready = await prepare_review_repo(
                backend,
                work_dir=work_dir,
                repo_owner=pr.owner,
                repo_name=pr.repo,
                head_sha=head.head.sha,
                pr_number=pr.number,
                base_sha=head.base.sha,
            )
            if not ready:
                raise RuntimeError("review guide could not check out the pull request")
            await git.pin(backend, repo_dir, base_sha=head.base.sha, head_sha=head.head.sha)
            _, view_error = await set_view(
                session.slack_channel_id,
                "diff",
                content=await git.pr_diff(backend, repo_dir, zero=False),
                base_branch=head.base.ref,
                head_branch=head.head.ref,
            )
            if view_error:
                logger.warning(
                    "Could not attach the pull request diff to a review guide channel",
                    extra={"agent_thread_id": self._thread_id, "slack_error": view_error},
                )
        # Refreshed every run so channels opened before a link was added still get it.
        await set_context_bar(
            session.slack_channel_id,
            repo_context_bar_items(
                {"owner": pr.owner, "name": pr.repo},
                pr_url=pr.url,
                dashboard_url=dashboard_thread_url(self._thread_id) or "",
            ),
        )
        await resume(session)
        changes = parse(await git.pr_diff(backend, repo_dir))
        walk = session.walk
        moved = walk is not None and walk.head_sha != head.head.sha
        if walk is not None and moved:
            walk, gone = walk.moved_to(head.head.sha, changes)
            await session.save_walk(walk)
            for group in gone:
                if group.status == "shown":
                    await retire(
                        session.slack_channel_id,
                        group.message_ts,
                        group.message_text,
                        "The pull request changed these lines",
                    )
            logger.info(
                "Review guide walkthrough carried to a newer head",
                extra={
                    "agent_thread_id": self._thread_id,
                    "pr_number": pr.number,
                    "guide_head_sha": head.head.sha,
                },
            )
        if walk is None:
            walk = Walk.start(head.head.sha, changes)
        approve_ts = RunConfig.from_config(self._config).review_guide_approve_ts
        approved = await approve_click(session, walk, approve_ts) if approve_ts else None
        status = walk.status(walk.unseen(changes, await session.seen_lines()))
        updates["messages"] = [
            HumanMessage(
                content=prompt(
                    "review-guide/state",
                    moved=moved,
                    head_sha=head.head.sha,
                    clicked=bool(approve_ts),
                    approved=approved.title if approved else "",
                    status=status.model_dump(),
                )
            )
        ]
        return updates


def _make_model_or_defer(model_id: str, *, use_gateway: bool, **kwargs: Any) -> BaseChatModel:
    try:
        return make_model(model_id, use_gateway=use_gateway, **kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("Deferring review guide model setup failure for %s", model_id, exc_info=True)
        return make_deferred_error_model(e, model_id=model_id)


async def get_review_guide(config: RunnableConfig) -> Pregel:
    config = config.copy()
    configurable = dict(config.get("configurable") or {})
    config["configurable"] = configurable
    config.setdefault("recursion_limit", DEFAULT_RECURSION_LIMIT)
    cfg = RunConfig.parse(configurable)
    thread_id = cfg.thread_id

    if thread_id is None or not graph_loaded_for_execution(config):
        return create_deep_agent(system_prompt="", tools=[]).with_config(bindable_config(config))

    settings = await cached_workspace_settings(cfg.workspace_slug)
    model_id, effort = settings.review_scout_model
    model_id, effort = gate_fable_model(model_id, effort, fable_enabled=settings.fable_enabled)
    model = _make_model_or_defer(
        model_id,
        use_gateway=settings.effective_gateway_enabled,
        **provider_model_kwargs(
            model_id,
            effort,
            max_tokens=DEFAULT_LLM_MAX_TOKENS,
            openai_reasoning_default=DEFAULT_LLM_REASONING,
        ),
    )

    session = await ReviewGuideSession.get(thread_id)
    closing_tools = (
        [mark_pull_request_ready, record_author_feedback]
        if session is not None and session.mode == "author"
        else [approve_pull_request]
    )

    async def reconnect_backend(_thread_id: str = thread_id) -> SandboxBackendProtocol:
        session = await ReviewGuideSession.get(_thread_id)
        if session is None:
            raise RuntimeError("review guide run has no session")
        pr = session.pull_request
        return await _ensure_guide_sandbox(_thread_id, pr.owner, pr.repo, session.workspace_slug)

    return create_deep_agent(
        model=model,
        system_prompt="",
        # A prepare run works in the background, so it gets nothing that posts to the channel.
        tools=apply_tool_descriptions(
            [queue_chunk, edit_queue, move_to_other]
            if cfg.review_guide_prefetch
            else [
                show_chunk,
                show_queued,
                queue_chunk,
                edit_queue,
                move_to_other,
                skip_changes,
                show_other,
                end_walkthrough,
                slack_reply,
                code_channel_set_view,
                *closing_tools,
            ]
        ),
        backend=get_cached_sandbox_backend(thread_id, reconnect=reconnect_backend),
        middleware=cast(
            list[AgentMiddleware[Any, Any, Any]],
            [
                PrepareReviewGuideRunMiddleware(thread_id=thread_id, config=config),
                SanitizeToolInputsMiddleware(),
                ModelCallLimitMiddleware(run_limit=GUIDE_MODEL_CALL_LIMIT, exit_behavior="end"),
                ToolErrorMiddleware(),
                TimeoutWrapupMiddleware(),
                SanitizeFireworksMessagesMiddleware(),
                SanitizeOpenAIResponsesMiddleware(),
                SanitizeThinkingBlocksMiddleware(),
                RepairOrphanedToolCallsMiddleware(),
                StableToolResultOrderMiddleware(),
                ModelErrorMiddleware(),
                ModelCallTimeoutMiddleware(),
            ],
        ),
    ).with_config(bindable_config(config))


# langgraph.json entrypoint. Runs trace into LANGSMITH_PROJECT like everything else.
traced_review_guide = get_review_guide
