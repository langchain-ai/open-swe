"""Reviewer graph factory.

Mirrors `agent.server.get_agent`'s sandbox lifecycle but configures a deep
agent for code review only:

- Deterministic repo prep (clone-or-fetch + checkout) before the agent's first
  model call so the LLM doesn't burn tokens narrating ``gh repo clone``.
- A computed unified diff and the set of (file, line) tuples in that diff,
  passed via the runnable config so ``add_finding`` can validate at creation
  time rather than failing at GitHub-publish time.
- A reviewer-specific tool set: ``add_finding``, ``update_finding``,
  ``list_findings``, ``publish_review``. No commit/push/PR-opening tools.
- A system prompt that pins the single-evolving-findings model, in-diff-only
  discipline, severity ladder, and the watch-mode reconciliation flow.
"""
# ruff: noqa: E402

import asyncio
import logging
import posixpath
import re
import warnings
from typing import Any, NotRequired, cast

logger = logging.getLogger(__name__)

from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel
from langgraph.runtime import Runtime

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

from deepagents import create_deep_agent
from deepagents.backends.protocol import SandboxBackendProtocol
from deepagents.middleware.skills import SkillsMiddleware, SkillsState
from deepagents.middleware.subagents import SubAgent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models.chat_models import BaseChatModel

from agent.dashboard.team_settings import (
    get_effective_gateway_enabled,
    get_org_review_guidelines,
    get_team_default_grouping_model,
    get_team_default_model_pair,
    get_team_fable_enabled,
)
from agent.github.app import get_github_app_installation_token_with_expiry
from agent.github.thread_token import cache_github_token_for_thread
from agent.middleware import (
    ToolErrorMiddleware,
    check_message_queue_before_model,
    refresh_github_proxy_before_model,
    settle_review_check_on_exit,
)
from agent.middleware.sandbox_circuit_breaker import post_sandbox_unreachable_notification
from agent.review.diff import (
    changed_files,
    compute_diff_line_set,
    fetch_pr_diff,
    fetch_pr_metadata,
    materialize_review_diff,
    review_diff_range,
)
from agent.review.findings import Finding
from agent.review.findings import (
    list_findings as list_findings_async,
)
from agent.review.groups import maybe_generate_and_store_diff_groups
from agent.review.publish import fetch_pr_review_threads
from agent.review.reconcile import reconcile_findings_with_review_threads
from agent.run_config import RunConfig
from agent.runtime import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_RECURSION_LIMIT,
    MODEL_CALL_RECURSION_LIMIT,
    bindable_config,
    ensure_sandbox_for_thread,
    get_cached_sandbox_backend,
    graph_loaded_for_execution,
)
from agent.tools import (
    add_finding,
    fetch_review_diff,
    http_request,
    list_findings,
    publish_review,
    reply_to_finding_thread,
    resolve_finding_thread,
    update_finding,
    web_search,
)
from agent.utils.api_standards_skill import fetch_api_standards_skill
from coding_agent.middleware import (
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
)
from coding_agent.middleware.prepare_run import PrepareRunState
from coding_agent.models import gate_fable_model
from coding_agent.prompts import apply_tool_descriptions, load_prompt, render_prompt
from coding_agent.sandboxes.paths import resolve_sandbox_work_dir
from coding_agent.sandboxes.repo_prep import materialize_trusted_skills, prepare_review_repo
from coding_agent.sandboxes.state import SandboxUnreachableError
from coding_agent.tools import fetch_url
from coding_agent.utils import ttl_cache
from coding_agent.utils.agents_md import fetch_agents_md, fetch_scoped_agents_md
from coding_agent.utils.deferred_model import make_deferred_error_model
from coding_agent.utils.model import DEFAULT_LLM_REASONING, make_model, provider_model_kwargs

HISTORICAL_REVIEW_GUIDANCE = load_prompt("reviewer/historical-guidance.md")

REVIEWER_PROMPT_TEMPLATE = load_prompt("reviewer/main.md")


REVIEWER_EVAL_PROMPT_SUFFIX = load_prompt("reviewer/eval.md")

REVIEWER_SUBAGENT_SYSTEM_PROMPT = load_prompt("reviewer/subagent.md")


def _reviewer_subagent(model: BaseChatModel) -> SubAgent:
    return {
        "name": "reviewer",
        "description": load_prompt("reviewer/subagent-description.md"),
        "system_prompt": REVIEWER_SUBAGENT_SYSTEM_PROMPT,
        "model": model,
        # Subagents compile into their own graphs, so the reviewer's own
        # middleware never wraps their model calls.
        "middleware": cast(
            list[AgentMiddleware[Any, Any, Any]],
            [
                SanitizeOpenAIResponsesMiddleware(),
                ModelErrorMiddleware(),
                ModelCallTimeoutMiddleware(),
            ],
        ),
    }


def _repo_checkout_note(
    *,
    repo_ready: bool,
    working_dir: str,
    repo_owner: str,
    repo_name: str,
    pr_number: int | str,
    head_sha: str,
) -> str:
    if repo_ready:
        return render_prompt("reviewer/repo-ready.md", working_dir=working_dir)
    return render_prompt(
        "reviewer/repo-not-ready.md",
        working_dir=working_dir,
        parent_dir=posixpath.dirname(working_dir) or working_dir,
        repo_owner=repo_owner or "<owner>",
        repo_name=repo_name or "<repo>",
        pr_number=pr_number if pr_number != "" else "<pr_number>",
        head_sha=head_sha or "<head_sha>",
    )


def _reviewer_system_prompt(
    working_dir: str,
    *,
    repo_owner: str,
    repo_name: str,
    pr_number: int | str,
    repo_ready: bool = True,
    head_sha: str = "",
    reviewer_eval: bool = False,
    org_guidelines: str | None = None,
    repo_style_prompt: str | None = None,
    agents_md_content: str | None = None,
    scoped_agents_md: dict[str, str] | None = None,
    api_standards_skill: str | None = None,
) -> str:
    prompt = render_prompt(
        "reviewer/main.md",
        working_dir=working_dir,
        repo_owner=repo_owner or "<owner>",
        repo_name=repo_name or "<repo>",
        pr_number=pr_number if pr_number != "" else "<pr_number>",
        historical_review_guidance="" if reviewer_eval else HISTORICAL_REVIEW_GUIDANCE,
        repo_checkout_note=_repo_checkout_note(
            repo_ready=repo_ready,
            working_dir=working_dir,
            repo_owner=repo_owner,
            repo_name=repo_name,
            pr_number=pr_number,
            head_sha=head_sha,
        ),
    )
    if reviewer_eval:
        prompt = f"{prompt}\n{REVIEWER_EVAL_PROMPT_SUFFIX}"
    if org_guidelines:
        prompt = (
            f"{prompt}\n\n"
            "# Organization-wide review guidelines\n\n"
            "These guidelines were set by a workspace admin and apply to every "
            "repository this reviewer covers. Apply them when they agree with the "
            "global bar above; they refine tone, severity, and what this "
            "organization typically flags. Repository-specific rules below take "
            "precedence when they conflict.\n\n"
            f"{org_guidelines}"
        )
    if repo_style_prompt:
        prompt = (
            f"{prompt}\n\n"
            "# Repository-specific review style\n\n"
            "The following rules were learned from this repository's historical "
            "PR reviews. Apply them when they agree with the global bar above; "
            "they refine tone, severity, and what this team typically flags.\n\n"
            f"{repo_style_prompt}"
        )
    if agents_md_content or scoped_agents_md:
        instruction_sections: list[str] = []
        if agents_md_content:
            instruction_sections.append(
                f"## Root instructions (scope: entire repository)\n\n```\n{agents_md_content}\n```"
            )
        for path, content in (scoped_agents_md or {}).items():
            scope = posixpath.dirname(path)
            instruction_sections.append(
                f"## Instructions from `{path}` (scope: `{scope}/`)\n\n```\n{content}\n```"
            )
        prompt = (
            f"{prompt}\n\n"
            "# Repository conventions (AGENTS.md / CLAUDE.md)\n\n"
            "The following files come from the target branch (the PR's base), "
            "not from the PR head. They document the "
            "project's conventions, architecture, and rules. These rules are "
            "**mandatory** — the project enforces them on every contributor and "
            "they are not optional style preferences. When a changed line "
            "violates one of these rules, file a finding for it (still anchored "
            "to the changed line, still a concrete failure mode, still in-diff). "
            "Do not file findings for pre-existing violations outside the diff.\n\n"
            "Common rule categories to check:\n"
            "- **Documentation sync rules** — many repos require docs/ to be "
            "updated when behavior changes. If the PR changes behavior a doc "
            "describes and the doc is not updated, that is a finding.\n"
            "- **Naming / convention rules** — if the repo mandates specific "
            "naming, patterns, or helpers and the PR uses the wrong one, that "
            "is a finding (not a style nit — it violates an explicit repo rule).\n"
            "- **Architecture / layering rules** — if the repo forbids certain "
            "imports, cross-layer calls, or patterns and the PR introduces one, "
            "that is a finding.\n"
            "- **Process / CI rules** — if the repo requires tests, changelog "
            "entries, or specific CI steps for certain changes and the PR skips "
            "them, that is a finding.\n\n"
            "Each scoped `AGENTS.md` applies only to changed files under its "
            "listed directory. When instructions conflict, the most deeply "
            "nested applicable file takes precedence.\n\n" + "\n\n".join(instruction_sections)
        )
    if api_standards_skill:
        prompt = (
            f"{prompt}\n\n"
            "# API standards skill\n\n"
            "Apply this skill ONLY when the PR introduces a new API or modifies "
            "an existing one (HTTP routes/handlers, RPC or GraphQL endpoints, "
            "public SDK/library signatures, request/response schemas, status "
            "codes, headers, or other API contracts). When the diff touches such "
            "surfaces, verify the change against the best practices below and "
            "file a finding when a changed line violates them and clears the "
            "global bar above (anchored, concrete failure mode, in-diff). If the "
            "PR does not change any API, ignore this section. Do not file "
            "style-only nits or pre-existing violations outside the diff.\n\n"
            f"{api_standards_skill}"
        )
    return prompt


def _format_pr_overview(pr_title: str, pr_body: str) -> str:
    """Render the PR title and body as an untrusted-data block.

    Both fields are author-controlled text from the PR — anyone who can open
    or edit a PR can put anything here, including prompt-injection payloads.
    We wrap them in an XML data block and neutralize the closing tag so the
    body can't break out, mirroring how existing PR review threads are
    handled. Returns ``""`` when there is nothing to show.
    """
    title = pr_title.strip() if isinstance(pr_title, str) else ""
    body = pr_body.strip() if isinstance(pr_body, str) else ""
    if not title and not body:
        return ""
    safe_title = _escape_for_data_block(title)
    safe_body = _escape_for_data_block(body) if body else "_(no description provided)_"
    return render_prompt("reviewer/pr-overview.md", title=safe_title, body=safe_body)


def _build_first_review_context(
    *,
    pr_url: str,
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    pr_title: str = "",
    pr_body: str = "",
    existing_threads_block: str = "",
    include_historical_guidance: bool = True,
) -> str:
    overview = _format_pr_overview(pr_title, pr_body)
    overview_section = f"\n{overview}" if overview else ""
    prior_section = (
        f"\n## Pre-existing PR review threads\n\n{existing_threads_block}\n"
        if existing_threads_block
        else ""
    )
    historical_guidance = (
        " If a Pre-existing PR review threads section is present, do not "
        "re-file anything that overlaps one of those threads."
        if include_historical_guidance
        else ""
    )
    return render_prompt(
        "reviewer/first-review-context.md",
        repo_owner=repo_owner,
        repo_name=repo_name,
        pr_number=pr_number,
        pr_url=pr_url,
        base_sha=base_sha,
        head_sha=head_sha,
        overview_section=overview_section,
        prior_section=prior_section,
        historical_guidance=historical_guidance,
    )


def _build_re_review_context(
    *,
    pr_url: str,
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    last_reviewed_sha: str,
    head_sha: str,
    existing_findings_block: str,
    pr_title: str = "",
    pr_body: str = "",
    existing_threads_block: str = "",
) -> str:
    overview = _format_pr_overview(pr_title, pr_body)
    overview_section = f"{overview}\n" if overview else ""
    prior_threads_section = (
        f"## Pre-existing PR review threads\n\n{existing_threads_block}\n\n"
        if existing_threads_block
        else ""
    )
    return render_prompt(
        "reviewer/rereview-context.md",
        repo_owner=repo_owner,
        repo_name=repo_name,
        pr_number=pr_number,
        pr_url=pr_url,
        last_reviewed_sha=last_reviewed_sha,
        head_sha=head_sha,
        overview_section=overview_section,
        existing_findings=existing_findings_block,
        prior_threads_section=prior_threads_section,
    )


def _build_finding_reply_context(
    *,
    pr_url: str,
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    finding_id: str,
    reply_author: str,
    reply_body: str,
    existing_findings_block: str,
    pr_title: str = "",
    pr_body: str = "",
    existing_threads_block: str = "",
) -> str:
    overview = _format_pr_overview(pr_title, pr_body)
    overview_section = f"{overview}\n" if overview else ""
    prior_threads_section = (
        f"## Pre-existing PR review threads\n\n{existing_threads_block}\n\n"
        if existing_threads_block
        else ""
    )
    safe_author = _safe_login(reply_author)
    safe_reply_body = _escape_for_data_block(reply_body)
    return render_prompt(
        "reviewer/finding-reply-context.md",
        repo_owner=repo_owner,
        repo_name=repo_name,
        pr_number=pr_number,
        pr_url=pr_url,
        finding_id=finding_id,
        safe_author=safe_author,
        safe_reply_body=safe_reply_body,
        overview_section=overview_section,
        existing_findings=existing_findings_block,
        prior_threads_section=prior_threads_section,
    )


# GitHub login regex: alphanumerics or single hyphens, max 39 chars, optional
# trailing "[bot]" suffix. Logins that don't match are surfaced as "unknown"
# so we never let unexpected text leak through this field as a header.
_GITHUB_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}(?:\[bot\])?$")


def _safe_login(value: object) -> str:
    if isinstance(value, str) and _GITHUB_LOGIN_RE.match(value):
        return value
    return "unknown"


# Closing tags of the wrappers used in this module. XML tolerates whitespace
# around the tag name (e.g. `</body >`, `</ body\n>`), so a literal `.replace()`
# of the canonical spelling alone is insufficient — we match each end tag
# whitespace-tolerantly and rewrite it to an inert, human-readable form.
_DATA_BLOCK_WRAPPER_TAGS = (
    "pr_review_threads",
    "thread",
    "comment",
    "body",
    "pr_overview",
    "title",
)
_CLOSING_TAG_RE = re.compile(
    r"</\s*(" + "|".join(_DATA_BLOCK_WRAPPER_TAGS) + r")\s*>",
    re.IGNORECASE,
)


def _escape_for_data_block(text: str) -> str:
    """Neutralize closing tags so an attacker-controlled body can't break out.

    Matches each wrapper's end tag whitespace-tolerantly (XML allows whitespace
    before/after the tag name) and rewrites it to an inert ``</name_>`` form
    that stays human-readable but is no longer a valid closer.
    """
    return _CLOSING_TAG_RE.sub(lambda m: f"</{m.group(1).lower()}_>", text)


def _format_pr_review_threads(threads: list[dict]) -> str:
    """Render existing PR review threads as an XML-wrapped data block.

    The block goes into the reviewer's system prompt, so the comment bodies
    inside are attacker-controlled text from the PR (anyone who can comment
    on a PR can put anything in here, including "ignore all previous
    instructions" payloads). We wrap the whole block — and each body
    individually — in XML tags and tell the agent in the system prompt that
    everything inside ``<pr_review_threads>`` is untrusted *data* to read,
    never instructions to follow. We additionally:

    - sanitize author logins against the GitHub username grammar so the
      ``author`` attribute can't carry freeform text,
    - neutralize literal closing tags in bodies so a body can't break out
      of its wrapper.

    Modern frontier models are well-trained to treat clearly-delimited data
    sections as data; the wrapping is the contract.
    """
    if not threads:
        return ""
    visible: list[dict] = []
    for t in threads:
        comments = t.get("comments") or []
        if not comments:
            continue
        visible.append(t)
    if not visible:
        return ""

    def _sort_key(t: dict) -> tuple[int, int, str, int]:
        # Open + non-outdated first; then by path/line for stability.
        priority = 0 if not t.get("is_resolved") and not t.get("is_outdated") else 1
        return (
            priority,
            0 if not t.get("is_resolved") else 1,
            t.get("path") or "",
            t.get("line") or t.get("original_line") or 0,
        )

    visible.sort(key=_sort_key)

    out: list[str] = ["<pr_review_threads>"]
    for t in visible:
        path = t.get("path") or "<unknown>"
        line = t.get("line") if isinstance(t.get("line"), int) else t.get("original_line")
        location = f"{path}:{line}" if isinstance(line, int) else path
        status: str
        if t.get("is_resolved"):
            status = "resolved"
        elif t.get("is_outdated"):
            status = "outdated"
        else:
            status = "open"
        # Path is already validated by GitHub's file-path rules but treat it
        # defensively for the attribute (no quotes, no closing-bracket).
        safe_location = location.replace('"', "&quot;").replace(">", "&gt;")
        out.append(f'  <thread location="{safe_location}" status="{status}">')
        for c in t.get("comments") or []:
            if not isinstance(c, dict):
                continue
            login = _safe_login(c.get("author"))
            body_raw = c.get("body") or ""
            if not isinstance(body_raw, str):
                body_raw = ""
            # Trim very long bodies so a single comment can't blow up context.
            if len(body_raw) > 4000:  # noqa: PLR2004
                body_raw = body_raw[:4000] + "\n...[truncated]"
            body_safe = _escape_for_data_block(body_raw)
            out.append(f'    <comment author="{login}">')
            out.append("      <body>")
            out.append(body_safe)
            out.append("      </body>")
            out.append("    </comment>")
        out.append("  </thread>")
    out.append("</pr_review_threads>")
    return "\n".join(out)


def _format_existing_findings(findings: list[Finding]) -> str:
    if not findings:
        return "_(none)_"
    lines: list[str] = []
    for f in findings:
        if f.get("status") != "open":
            continue
        location = f["file"]
        start = f["start_line"]
        end = f["end_line"]
        if start is not None and end is not None:
            location += f":{start}" if start == end else f":{start}-{end}"
        title = f.get("title")
        title_prefix = f"{title}: " if isinstance(title, str) and title.strip() else ""
        lines.append(
            f"- [{f['id']}] ({f['severity']}, {f['category']}) "
            f"{location} — {title_prefix}{f['description'].strip()}"
        )
        human_reply = f.get("last_human_reply_body")
        if isinstance(human_reply, str) and human_reply:
            author = f.get("last_human_reply_author") or "human"
            lines.append(f"  Human reply from {author}: {human_reply}")
    return "\n".join(lines) if lines else "_(no open findings)_"


# Strong references to fire-and-forget background tasks (e.g. the AI-sorted
# diff grouping pass) so the event loop doesn't garbage-collect them mid-flight.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def _make_model_or_defer(
    model_id: str,
    *,
    use_gateway: bool,
    **kwargs: Any,
) -> BaseChatModel:
    try:
        return make_model(model_id, use_gateway=use_gateway, **kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("Deferring reviewer model setup failure for %s", model_id, exc_info=True)
        return make_deferred_error_model(e, model_id=model_id)


def _on_background_task_done(task: asyncio.Task[None]) -> None:
    _BACKGROUND_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("Background reviewer task failed: %s", exc)


async def _resolve_grouping_model(cfg: RunConfig, *, use_gateway: bool) -> BaseChatModel:
    """Resolve the model for the diff-grouping pass.

    Per-run override (``grouping_model_id``/``grouping_reasoning_effort``) wins;
    otherwise the team default, which itself inherits the reviewer subagent
    model when no grouping-specific model is configured.
    """
    if cfg.grouping_model_id:
        model_id = cfg.grouping_model_id
        effort = cfg.grouping_reasoning_effort
    else:
        model_id, effort = await get_team_default_grouping_model()
    model_id, effort = gate_fable_model(
        model_id, effort, fable_enabled=await get_team_fable_enabled()
    )
    model_kwargs = provider_model_kwargs(
        model_id,
        effort,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
        openai_reasoning_default=DEFAULT_LLM_REASONING,
    )
    return _make_model_or_defer(model_id, use_gateway=use_gateway, **model_kwargs)


async def _cached_reviewer_team_defaults():
    return await ttl_cache.cached(
        "team-default-model-pair:reviewer",
        60,
        lambda: get_team_default_model_pair("reviewer"),
    )


async def _cached_gateway_enabled() -> bool:
    return await ttl_cache.cached(
        "team:gateway-enabled",
        60,
        get_effective_gateway_enabled,
    )


async def _cached_org_review_guidelines() -> str | None:
    return await ttl_cache.cached(
        "reviewer:org-guidelines",
        300,
        get_org_review_guidelines,
    )


async def _cached_api_standards_skill() -> str | None:
    return await ttl_cache.cached(
        "reviewer:api-standards-skill",
        300,
        fetch_api_standards_skill,
    )


class PrepareReviewerRunState(PrepareRunState):
    diff_text: NotRequired[str]
    diff_line_set: NotRequired[dict[str, dict[str, set[int]]] | None]


async def _ensure_reviewer_sandbox_for_thread(
    thread_id: str,
    cfg: RunConfig,
) -> tuple[SandboxBackendProtocol, str | None]:
    repo_name = cfg.repo.name if cfg.repo else ""
    github_token: str | None = None
    if cfg.source:
        github_token, expires_at = await get_github_app_installation_token_with_expiry(
            repositories=[repo_name] if repo_name else None
        )
        if not github_token:
            raise RuntimeError(
                f"GitHub App installation token unavailable for reviewer thread {thread_id}"
            )
        cache_github_token_for_thread(
            thread_id, github_token, expires_at=expires_at, is_bot_token=True
        )

    return (
        await ensure_sandbox_for_thread(
            thread_id,
            github_proxy_token=github_token,
            github_proxy_repositories=[repo_name] if repo_name else None,
            # A reviewer sandbox holds nothing but a checkout `prepare_review_repo`
            # re-derives every run, and reviewer threads outlive their sandbox: one
            # thread per PR, re-triggered on every push. Refusing to replace an
            # unreachable one would brick reviews on that PR for good.
            allow_replacement=True,
        ),
        github_token,
    )


class PrepareReviewerRunMiddleware(BasePrepareRunMiddleware):
    state_schema = PrepareReviewerRunState

    def __init__(
        self,
        *,
        thread_id: str,
        config: RunnableConfig,
        use_gateway: bool,
    ) -> None:
        self._thread_id = thread_id
        self._config = config
        self._use_gateway = use_gateway

    def _prepare_config_fingerprint(self) -> Any:
        cfg = RunConfig.from_config(self._config)
        return {
            "prepare_run_id": cfg.prepare_run_id,
            "thread_id": self._thread_id,
            "repo": cfg.repo.model_dump() if cfg.repo else None,
            "pr_number": cfg.pr_number,
            "base_sha": cfg.base_sha,
            "head_sha": cfg.head_sha,
            "last_reviewed_sha": cfg.last_reviewed_sha,
            "reviewer_event": cfg.reviewer_event,
            "reviewer_eval": cfg.reviewer_eval,
            "eval": cfg.eval,
            "finding_reply_id": cfg.finding_reply_id,
        }

    async def _prepare(self, state: PrepareRunState, runtime: Runtime) -> dict[str, Any]:
        cfg = RunConfig.from_config(self._config)
        try:
            sandbox_backend, github_token = await _ensure_reviewer_sandbox_for_thread(
                self._thread_id, cfg
            )
        except SandboxUnreachableError as exc:
            # Replacement was allowed and still failed, so this run dies without a
            # sandbox. Say so on the PR instead of leaving it looking unreviewed.
            await post_sandbox_unreachable_notification(
                self._config or {}, sandbox_id=exc.sandbox_id, replacement_attempted=True
            )
            raise
        work_dir = await resolve_sandbox_work_dir(sandbox_backend)

        repo_owner = cfg.repo.owner if cfg.repo else ""
        repo_name = cfg.repo.name if cfg.repo else ""
        base_sha = cfg.base_sha or ""
        head_sha = cfg.head_sha or ""
        pr_number = cfg.pr_number

        repo_ready = await prepare_review_repo(
            sandbox_backend,
            work_dir=work_dir,
            repo_owner=repo_owner,
            repo_name=repo_name,
            head_sha=head_sha,
            pr_number=pr_number,
            base_sha=base_sha,
        )
        skill_sources: list[str] = []
        if repo_ready and repo_name:
            skill_sources = await materialize_trusted_skills(
                sandbox_backend, repo_dir=f"{work_dir}/{repo_name}", trusted_ref=base_sha
            )

        pr_url = cfg.pr_url or ""
        last_reviewed_sha = cfg.last_reviewed_sha or ""
        is_re_review = bool(cfg.re_review)
        reviewer_event = cfg.reviewer_event or ""
        reviewer_eval = cfg.is_eval
        can_fetch_pr = (
            pr_number is not None and bool(repo_owner) and bool(repo_name) and bool(github_token)
        )

        async def _fetch_diff_context() -> tuple[str, dict[str, dict[str, set[int]]] | None]:
            if not can_fetch_pr or github_token is None or not isinstance(pr_number, int):
                return "", None
            fetched_diff: str | None = None
            if not (is_re_review and last_reviewed_sha):
                fetched_diff = await fetch_pr_diff(
                    owner=repo_owner,
                    repo=repo_name,
                    pr_number=pr_number,
                    token=github_token,
                )
                if fetched_diff is None:
                    return "", None
            try:
                diff_base, diff_head, merge_base = review_diff_range(
                    base_sha=base_sha,
                    head_sha=head_sha,
                    last_reviewed_sha=last_reviewed_sha,
                    re_review=is_re_review,
                )
                materialized = await materialize_review_diff(
                    sandbox_backend,
                    work_dir=f"{work_dir}/{repo_name}",
                    base_ref=diff_base,
                    head_ref=diff_head,
                    merge_base=merge_base,
                    diff_text=fetched_diff,
                )
                diff_text = materialized.diff_text
            except RuntimeError, ValueError:
                logger.exception("Failed to materialize review diff")
                if fetched_diff is None:
                    return "", None
                diff_text = fetched_diff
            return diff_text, compute_diff_line_set(diff_text)

        async def _fetch_pr_overview() -> tuple[str, str]:
            if not can_fetch_pr or github_token is None or not isinstance(pr_number, int):
                return "", ""
            metadata = await fetch_pr_metadata(
                owner=repo_owner,
                repo=repo_name,
                pr_number=pr_number,
                token=github_token,
            )
            return metadata if metadata is not None else ("", "")

        async def _fetch_existing_threads_block() -> str:
            if (
                reviewer_eval
                or not can_fetch_pr
                or github_token is None
                or not isinstance(pr_number, int)
            ):
                return ""
            try:
                threads = await fetch_pr_review_threads(
                    owner=repo_owner,
                    repo=repo_name,
                    pr_number=pr_number,
                    token=github_token,
                )
                await reconcile_findings_with_review_threads(self._thread_id, threads)
                block = _format_pr_review_threads(threads)
                if block:
                    logger.info(
                        "Loaded %d existing PR review thread(s) into reviewer context for %s/%s#%s",
                        len(threads),
                        repo_owner,
                        repo_name,
                        pr_number,
                    )
                return block
            except Exception:
                logger.exception(
                    "Failed to load existing PR review threads for %s/%s#%s; continuing without comment-awareness context",
                    repo_owner,
                    repo_name,
                    pr_number,
                )
                return ""

        async def _fetch_repo_style_prompt() -> str | None:
            if not repo_owner or not repo_name:
                return None
            from agent.review.styles import get_repo_custom_prompt

            return await get_repo_custom_prompt(repo_owner, repo_name)

        async def _fetch_agents_md_context() -> str | None:
            if not repo_owner or not repo_name or not base_sha:
                return None
            content = await fetch_agents_md(repo_owner, repo_name, base_sha, token=github_token)
            if content:
                logger.info(
                    "Loaded AGENTS.md (%d chars) from %s/%s@%s into reviewer prompt",
                    len(content),
                    repo_owner,
                    repo_name,
                    base_sha,
                )
            return content

        diff_context_task = asyncio.create_task(_fetch_diff_context())
        pr_overview_task = asyncio.create_task(_fetch_pr_overview())
        existing_threads_task = asyncio.create_task(_fetch_existing_threads_block())
        repo_style_task = asyncio.create_task(_fetch_repo_style_prompt())
        agents_md_task = asyncio.create_task(_fetch_agents_md_context())
        org_guidelines_task = asyncio.create_task(_cached_org_review_guidelines())
        api_standards_task = asyncio.create_task(_cached_api_standards_skill())
        diff_context = await diff_context_task
        pr_diff_text, pr_diff_line_set = diff_context
        scoped_agents_md_task = asyncio.create_task(
            fetch_scoped_agents_md(
                repo_owner,
                repo_name,
                base_sha,
                changed_files(pr_diff_text),
                token=github_token,
            )
        )
        pr_overview = await pr_overview_task
        existing_threads_block = await existing_threads_task
        repo_style_prompt = await repo_style_task
        agents_md_content = await agents_md_task
        scoped_agents_md = await scoped_agents_md_task
        org_guidelines = await org_guidelines_task
        api_standards_skill = await api_standards_task
        pr_title, pr_body = pr_overview

        review_context = ""
        if pr_number is not None:
            if reviewer_event == "finding_reply":
                existing_findings = await list_findings_async(self._thread_id)
                review_context = _build_finding_reply_context(
                    pr_url=pr_url,
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                    pr_number=pr_number,
                    finding_id=cfg.finding_reply_id or "",
                    reply_author=cfg.finding_reply_author or "",
                    reply_body=cfg.finding_reply_body or "",
                    existing_findings_block=_format_existing_findings(existing_findings),
                    pr_title=pr_title,
                    pr_body=pr_body,
                    existing_threads_block=existing_threads_block,
                )
            elif is_re_review and last_reviewed_sha:
                existing_findings = await list_findings_async(self._thread_id)
                review_context = _build_re_review_context(
                    pr_url=pr_url,
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                    pr_number=pr_number,
                    last_reviewed_sha=last_reviewed_sha,
                    head_sha=head_sha,
                    existing_findings_block=_format_existing_findings(existing_findings),
                    pr_title=pr_title,
                    pr_body=pr_body,
                    existing_threads_block=existing_threads_block,
                )
            else:
                review_context = _build_first_review_context(
                    pr_url=pr_url,
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                    pr_number=pr_number,
                    base_sha=base_sha,
                    head_sha=head_sha,
                    pr_title=pr_title,
                    pr_body=pr_body,
                    existing_threads_block=existing_threads_block,
                    include_historical_guidance=not reviewer_eval,
                )

        system_prompt = _reviewer_system_prompt(
            f"{work_dir}/{repo_name}" if repo_name else work_dir,
            repo_owner=repo_owner,
            repo_name=repo_name,
            pr_number=pr_number if isinstance(pr_number, int) else "",
            repo_ready=repo_ready,
            head_sha=head_sha,
            reviewer_eval=reviewer_eval,
            org_guidelines=org_guidelines,
            repo_style_prompt=repo_style_prompt,
            agents_md_content=agents_md_content,
            scoped_agents_md=scoped_agents_md,
            api_standards_skill=api_standards_skill,
        )
        if review_context:
            system_prompt = f"{system_prompt}\n\n{review_context}"
        if skill_sources:
            skill_middleware = SkillsMiddleware(backend=sandbox_backend, sources=skill_sources)
            skill_update = (
                await skill_middleware.abefore_agent(
                    cast(SkillsState, {}),
                    runtime,
                    self._config,
                )
                or {}
            )
            skill_request_state = {
                "skills_metadata": skill_update.get("skills_metadata", []),
                "skills_load_errors": skill_update.get("skills_load_errors", []),
            }
            # Deepagents exposes no public formatter for the skills prompt sections.
            skills_locations = skill_middleware._format_skills_locations()  # noqa: SLF001
            skills_list = skill_middleware._format_skills_list(  # noqa: SLF001
                skill_request_state["skills_metadata"]
            )
            skills_load_warnings = skill_middleware._format_skills_load_warnings(  # noqa: SLF001
                skill_request_state["skills_load_errors"]
            )
            if skill_middleware.system_prompt_template:
                system_prompt = (
                    f"{system_prompt}\n\n"
                    + skill_middleware.system_prompt_template.format(
                        skills_locations=skills_locations,
                        skills_load_warnings=skills_load_warnings,
                        skills_list=skills_list,
                    )
                )

        if reviewer_event != "finding_reply" and pr_diff_text and self._thread_id:
            grouping_model = await _resolve_grouping_model(cfg, use_gateway=self._use_gateway)
            grouping_task = asyncio.create_task(
                maybe_generate_and_store_diff_groups(
                    thread_id=self._thread_id,
                    head_sha=head_sha,
                    diff_text=pr_diff_text,
                    model=grouping_model,
                )
            )
            _BACKGROUND_TASKS.add(grouping_task)
            grouping_task.add_done_callback(_on_background_task_done)

        return {
            "work_dir": work_dir,
            "rendered_system_prompt": system_prompt,
            "diff_text": pr_diff_text,
            "diff_line_set": pr_diff_line_set,
        }


async def get_reviewer_agent(config: RunnableConfig) -> Pregel:
    """Get or create a reviewer agent with checkpointed run prep."""
    config = config.copy()
    configurable = dict(config.get("configurable") or {})
    config["configurable"] = configurable
    config.setdefault("recursion_limit", DEFAULT_RECURSION_LIMIT)
    cfg = RunConfig.parse(configurable)
    thread_id = cfg.thread_id

    if thread_id is None or not graph_loaded_for_execution(config):
        logger.info("No thread_id or not for execution, returning reviewer agent without sandbox")
        return create_deep_agent(system_prompt="", tools=[]).with_config(bindable_config(config))

    if cfg.reviewer_model_id:
        model_id = cfg.reviewer_model_id
        reasoning_effort = cfg.reviewer_reasoning_effort
        subagent_model_id = model_id
        subagent_effort = reasoning_effort
    else:
        (
            (model_id, reasoning_effort),
            (subagent_model_id, subagent_effort),
        ) = await _cached_reviewer_team_defaults()
        logger.info(
            "Using team default reviewer model: model=%s effort=%s",
            model_id,
            reasoning_effort,
        )
        logger.info(
            "Using team default reviewer subagent model: model=%s effort=%s",
            subagent_model_id,
            subagent_effort,
        )
    if cfg.reviewer_subagent_model_id:
        subagent_model_id = cfg.reviewer_subagent_model_id
        subagent_effort = cfg.reviewer_subagent_reasoning_effort
    fable_enabled = await get_team_fable_enabled()
    model_id, reasoning_effort = gate_fable_model(
        model_id, reasoning_effort, fable_enabled=fable_enabled
    )
    subagent_model_id, subagent_effort = gate_fable_model(
        subagent_model_id, subagent_effort, fable_enabled=fable_enabled
    )
    model_kwargs = provider_model_kwargs(
        model_id,
        reasoning_effort,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
        openai_reasoning_default=DEFAULT_LLM_REASONING,
    )
    subagent_model_kwargs = provider_model_kwargs(
        subagent_model_id,
        subagent_effort,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
        openai_reasoning_default=DEFAULT_LLM_REASONING,
    )

    use_gateway = await _cached_gateway_enabled()
    reviewer_model = _make_model_or_defer(model_id, use_gateway=use_gateway, **model_kwargs)
    reviewer_subagent_model = _make_model_or_defer(
        subagent_model_id, use_gateway=use_gateway, **subagent_model_kwargs
    )

    async def reconnect_backend(
        _thread_id: str = thread_id,
        _cfg: RunConfig = cfg,
    ) -> SandboxBackendProtocol:
        sandbox_backend, _github_token = await _ensure_reviewer_sandbox_for_thread(_thread_id, _cfg)
        return sandbox_backend

    backend = get_cached_sandbox_backend(thread_id, reconnect=reconnect_backend)

    return create_deep_agent(
        model=reviewer_model,
        system_prompt="",
        tools=apply_tool_descriptions(
            [
                fetch_review_diff,
                add_finding,
                update_finding,
                list_findings,
                publish_review,
                resolve_finding_thread,
                reply_to_finding_thread,
                web_search,
                fetch_url,
                http_request,
            ]
        ),
        subagents=[_reviewer_subagent(reviewer_subagent_model)],
        backend=backend,
        middleware=cast(
            list[AgentMiddleware[Any, Any, Any]],
            [
                PrepareReviewerRunMiddleware(
                    thread_id=thread_id,
                    config=config,
                    use_gateway=use_gateway,
                ),
                SanitizeToolInputsMiddleware(),
                ModelCallLimitMiddleware(run_limit=MODEL_CALL_RECURSION_LIMIT, exit_behavior="end"),
                ToolErrorMiddleware(),
                refresh_github_proxy_before_model,
                check_message_queue_before_model,
                TimeoutWrapupMiddleware(),
                SanitizeFireworksMessagesMiddleware(),
                SanitizeOpenAIResponsesMiddleware(),
                SanitizeThinkingBlocksMiddleware(),
                RepairOrphanedToolCallsMiddleware(),
                StableToolResultOrderMiddleware(),
                ModelErrorMiddleware(),
                ModelCallTimeoutMiddleware(),
                settle_review_check_on_exit,
            ],
        ),
    ).with_config(bindable_config(config))


# langgraph.json entrypoint. Runs trace into LANGSMITH_PROJECT like everything else.
traced_reviewer_agent = get_reviewer_agent
