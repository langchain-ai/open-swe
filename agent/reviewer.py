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

import logging
import os
import warnings

logger = logging.getLogger(__name__)

from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

from deepagents import create_deep_agent
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware import ModelCallLimitMiddleware

from .middleware import (
    ExcludeToolsMiddleware,
    SanitizeToolInputsMiddleware,
    ToolErrorMiddleware,
)
from .reviewer_diff import (
    compute_diff_in_sandbox,
    compute_diff_line_set,
)
from .reviewer_findings import (
    list_findings as list_findings_async,
)
from .server import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_LLM_MODEL_ID,
    DEFAULT_LLM_REASONING,
    DEFAULT_RECURSION_LIMIT,
    MODEL_CALL_RECURSION_LIMIT,
    ensure_sandbox_for_thread,
    graph_loaded_for_execution,
)
from .tools import (
    add_finding,
    list_findings,
    publish_review,
    update_finding,
)
from .utils.auth import resolve_github_token
from .utils.github_token import get_github_token_from_thread
from .utils.model import ModelKwargs, make_model
from .utils.sandbox_paths import aresolve_sandbox_work_dir

REVIEWER_PROMPT_TEMPLATE = """You are an expert code reviewer.

Your job is to review one GitHub pull request, find real issues, record them
as structured findings, and publish a single GitHub review with the most
important findings as inline comments — with concrete suggestions where
possible so the user can click "Commit suggestion".

### Working environment

You are operating in a remote Linux sandbox at `{working_dir}`. The repository
has already been cloned and checked out to the PR head SHA before this run
started — you do **not** need to clone, fetch, or check out yourself.

- The `gh` CLI is installed and authenticated by a sandbox proxy. Always
  invoke it as `GH_TOKEN=dummy gh <command>`.
- The `execute` tool runs shell commands. Default timeout ~30 minutes.
- `read_file`, `grep`, `glob` are available for code exploration.

### How to review

1. The user message tells you which PR to review and includes the unified
   diff. **Review the diff that's there. Don't review pre-existing code.**
2. For each real issue you find in the diff, call **`add_finding`** with:
   - `severity`: one of `informational`, `low`, `medium`, `high`, `critical`.
     Calibrate strictly: `critical` = bug that breaks production or a security
     hole; `high` = real correctness/regression risk; `medium` = clear quality
     issue worth surfacing; `low` = small nit; `informational` = FYI / context,
     not a flaw. Inflated severities erode trust — be honest.
   - `category`: e.g. `correctness`, `security`, `perf`, `style`, `flag`.
   - `file`, `start_line`, `end_line`: anchor inside the PR diff. Use a range
     when the issue spans multiple lines (e.g. an entire function).
   - `description`: what's wrong, in 1–4 sentences. Markdown is fine.
   - `suggestion`: a concrete replacement for `start_line..end_line` whenever
     you can offer one. The published GitHub comment will render it as a
     ```suggestion``` block so the user can click "Commit suggestion".
3. When you've recorded every finding, call **`publish_review`** **exactly
   once** at the end of the run. It batches eligible findings into a single
   GitHub PR Review with inline comments + suggestion blocks, and stores the
   GitHub comment IDs back so re-reviews can later resolve threads.

### Re-reviewing on a new commit

If the user message says **"A new commit has been pushed"**, this is a
re-review. The message includes the existing findings list and the diff
**since the previous reviewed SHA**. Your job is to:

- For each existing **open** finding, decide whether the new commits:
  - **resolved** it — call `update_finding(id, status="resolved")`.
  - **left it unchanged** — do nothing.
  - **changed it materially** — call `update_finding` with a revised
    `severity`/`description`/`suggestion` and a `note` explaining the change.
- Review the new diff for any net-new issues and add them with `add_finding`
  as on a first review.
- Finally call `publish_review` once. It posts inline comments for the new
  findings and resolves the GitHub threads for findings that just moved to
  `resolved`.

You may use `list_findings()` at any time to inspect what's persisted.

### Hard rules

- **You are read-only.** Do NOT commit. Do NOT push. Do NOT open or update
  PRs. Do NOT use `gh pr review` or `gh api ... /reviews` directly — use the
  `publish_review` tool instead so the findings list and GitHub stay in sync.
- **Only review the diff.** Do not flag pre-existing code that the PR didn't
  touch. `add_finding` will reject ranges outside the PR diff.
- **One finding per distinct issue.** Don't split one bug into three findings,
  and don't merge unrelated issues into one.
- **Prefer suggestions where you have one.** A description without a fix is
  fine when there's no clear single-line fix; otherwise include the
  `suggestion` field so the user gets the "Commit suggestion" button.
- **Skip nits on a clean PR.** If you only have `informational`/`low`
  findings, that's fine — record them, then call `publish_review`. The
  default severity threshold hides them from GitHub but keeps them in state
  for the future UI.
"""


def _reviewer_system_prompt(working_dir: str) -> str:
    return REVIEWER_PROMPT_TEMPLATE.format(working_dir=working_dir)


async def _ensure_repo_checked_out(
    sandbox_backend: SandboxBackendProtocol,
    *,
    work_dir: str,
    owner: str,
    repo: str,
    base_sha: str,
    head_sha: str,
) -> None:
    """Clone-or-fetch + checkout the PR head into the sandbox.

    Idempotent: warm sandboxes that already have ``<work_dir>/<repo>`` just
    fetch new objects and re-check out; cold sandboxes clone from scratch.
    """
    repo_dir = f"{work_dir}/{repo}"
    script = (
        f"set -e; "
        f"if [ -d {repo_dir}/.git ]; then "
        f"  cd {repo_dir} && "
        f"  git fetch --no-tags origin {base_sha} {head_sha} && "
        f"  git checkout --force {head_sha}; "
        f"else "
        f"  GH_TOKEN=dummy gh repo clone {owner}/{repo} {repo_dir} -- --quiet && "
        f"  cd {repo_dir} && "
        f"  git fetch --no-tags origin {base_sha} {head_sha} && "
        f"  git checkout --force {head_sha}; "
        f"fi"
    )
    import asyncio

    await asyncio.to_thread(sandbox_backend.execute, script)


def _build_first_review_context(
    *,
    pr_url: str,
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    diff_text: str,
) -> str:
    return (
        f"## Pull request to review\n\n"
        f"- repo: {repo_owner}/{repo_name}\n"
        f"- pr_number: {pr_number}\n"
        f"- url: {pr_url}\n"
        f"- base_sha: {base_sha}\n"
        f"- head_sha: {head_sha}\n\n"
        f"## Unified diff (review only what's here)\n\n"
        f"```diff\n{diff_text}\n```\n\n"
        f"This is a first review — there are no existing findings. Record real "
        f"issues with `add_finding` (one per issue, with concrete `suggestion` "
        f"text whenever you can offer one), then call `publish_review` once at "
        f"the end."
    )


def _build_re_review_context(
    *,
    pr_url: str,
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    last_reviewed_sha: str,
    head_sha: str,
    diff_since_last_review: str,
    existing_findings_block: str,
) -> str:
    return (
        f"## A new commit has been pushed\n\n"
        f"- repo: {repo_owner}/{repo_name}\n"
        f"- pr_number: {pr_number}\n"
        f"- url: {pr_url}\n"
        f"- previous reviewed SHA: {last_reviewed_sha}\n"
        f"- new HEAD SHA: {head_sha}\n\n"
        f"## Existing findings\n\n{existing_findings_block}\n\n"
        f"## Diff since the previous reviewed SHA\n\n"
        f"```diff\n{diff_since_last_review}\n```\n\n"
        f"For each open finding above, decide whether the new commits resolved "
        f'it (`update_finding(id, status="resolved")`), left it unchanged '
        f"(no action), or changed it materially (`update_finding` with new "
        f"fields + a `note`). Then add any net-new findings introduced by the "
        f"new diff, and call `publish_review` once at the end."
    )


def _format_existing_findings(findings: list[dict]) -> str:
    if not findings:
        return "_(none)_"
    lines: list[str] = []
    for f in findings:
        if f.get("status") != "open":
            continue
        location = f.get("file", "<unknown>")
        start = f.get("start_line")
        end = f.get("end_line")
        if start is not None and end is not None:
            location += f":{start}" if start == end else f":{start}-{end}"
        lines.append(
            f"- [{f.get('id')}] ({f.get('severity')}, {f.get('category')}) "
            f"{location} — {f.get('description', '').strip()}"
        )
    return "\n".join(lines) if lines else "_(no open findings)_"


async def get_reviewer_agent(config: RunnableConfig) -> Pregel:
    """Get or create a reviewer agent with a sandbox + prepped repo."""
    thread_id = config["configurable"].get("thread_id", None)

    config["recursion_limit"] = DEFAULT_RECURSION_LIMIT

    if thread_id is None or not graph_loaded_for_execution(config):
        logger.info("No thread_id or not for execution, returning reviewer agent without sandbox")
        return create_deep_agent(system_prompt="", tools=[]).with_config(config)

    if config["configurable"].get("source"):
        cached_token, cached_encrypted = await get_github_token_from_thread(thread_id)
        if cached_token and cached_encrypted:
            config["metadata"]["github_token_encrypted"] = cached_encrypted
            del cached_token
        else:
            _token, new_encrypted = await resolve_github_token(config, thread_id)
            config["metadata"]["github_token_encrypted"] = new_encrypted
            del _token

    sandbox_backend = await ensure_sandbox_for_thread(thread_id)

    work_dir = await aresolve_sandbox_work_dir(sandbox_backend)

    repo_config = config["configurable"].get("repo") or {}
    repo_owner = str(repo_config.get("owner", ""))
    repo_name = str(repo_config.get("name", ""))
    base_sha = str(config["configurable"].get("base_sha", "") or "")
    head_sha = str(config["configurable"].get("head_sha", "") or "")
    pr_number = config["configurable"].get("pr_number")
    pr_url = str(config["configurable"].get("pr_url", "") or "")
    last_reviewed_sha = str(config["configurable"].get("last_reviewed_sha", "") or "")
    is_re_review = bool(config["configurable"].get("re_review"))

    diff_text = ""
    diff_line_set: dict[str, set[int]] = {}
    if repo_owner and repo_name and base_sha and head_sha:
        try:
            await _ensure_repo_checked_out(
                sandbox_backend,
                work_dir=work_dir,
                owner=repo_owner,
                repo=repo_name,
                base_sha=base_sha,
                head_sha=head_sha,
            )
            diff_base = last_reviewed_sha if is_re_review and last_reviewed_sha else base_sha
            diff_text = await compute_diff_in_sandbox(
                sandbox_backend,
                work_dir=f"{work_dir}/{repo_name}",
                base_ref=diff_base,
                head_ref=head_sha,
            )
            diff_line_set = compute_diff_line_set(diff_text)
        except Exception:
            logger.exception("Reviewer prep failed for thread %s", thread_id)

    config["configurable"]["diff_text"] = diff_text
    config["configurable"]["diff_line_set"] = {
        path: sorted(lines) for path, lines in diff_line_set.items()
    }

    review_context = ""
    if pr_number is not None and isinstance(pr_number, int):
        if is_re_review and last_reviewed_sha:
            existing_findings = await list_findings_async(thread_id)
            review_context = _build_re_review_context(
                pr_url=pr_url,
                repo_owner=repo_owner,
                repo_name=repo_name,
                pr_number=pr_number,
                last_reviewed_sha=last_reviewed_sha,
                head_sha=head_sha,
                diff_since_last_review=diff_text,
                existing_findings_block=_format_existing_findings(existing_findings),
            )
        else:
            review_context = _build_first_review_context(
                pr_url=pr_url,
                repo_owner=repo_owner,
                repo_name=repo_name,
                pr_number=pr_number,
                base_sha=base_sha,
                head_sha=head_sha,
                diff_text=diff_text,
            )

    model_id = os.environ.get("LLM_MODEL_ID", DEFAULT_LLM_MODEL_ID)
    model_kwargs: ModelKwargs = {"max_tokens": DEFAULT_LLM_MAX_TOKENS}
    if model_id == DEFAULT_LLM_MODEL_ID:
        model_kwargs["reasoning"] = DEFAULT_LLM_REASONING

    system_prompt = _reviewer_system_prompt(f"{work_dir}/{repo_name}" if repo_name else work_dir)
    if review_context:
        system_prompt = f"{system_prompt}\n\n{review_context}"

    return create_deep_agent(
        model=make_model(model_id, **model_kwargs),
        system_prompt=system_prompt,
        tools=[add_finding, update_finding, list_findings, publish_review],
        backend=sandbox_backend,
        middleware=[
            SanitizeToolInputsMiddleware(),
            ModelCallLimitMiddleware(run_limit=MODEL_CALL_RECURSION_LIMIT, exit_behavior="end"),
            ToolErrorMiddleware(),
            ExcludeToolsMiddleware(excluded=frozenset({"task"})),
        ],
    ).with_config(config)
