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
from langchain.agents.middleware import ModelCallLimitMiddleware

from .middleware import (
    ExcludeToolsMiddleware,
    SanitizeToolInputsMiddleware,
    SlackAssistantStatusMiddleware,
    ToolErrorMiddleware,
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
    _anthropic_effort_for,
    _anthropic_thinking_for,
    _openai_reasoning_for,
    ensure_sandbox_for_thread,
    graph_loaded_for_execution,
)
from .tools import (
    add_finding,
    fetch_url,
    http_request,
    list_findings,
    publish_review,
    update_finding,
    web_search,
)
from .utils.auth import resolve_github_token
from .utils.github_token import get_github_token_from_thread
from .utils.model import ModelKwargs, make_model
from .utils.sandbox_paths import aresolve_sandbox_work_dir

REVIEWER_PROMPT_TEMPLATE = """You are an expert code reviewer.

Your job is to review one GitHub pull request, surface only issues that are
*likely real and worth the author's attention*, and publish a single GitHub
review with inline comments — with a `suggestion` block only when the fix is
small enough (≤4 lines) to commit on sight.

**Precision is the priority.** A finding that turns out to be noise costs the
author trust in the entire tool. When in doubt, drop the finding.

### Working environment

You are operating in a remote Linux sandbox at `{working_dir}`.

- The `gh` CLI is installed and authenticated by a sandbox proxy. Always
  invoke it as `GH_TOKEN=dummy gh <command>`.
- The `execute` tool runs shell commands. Default timeout ~30 minutes.
- `read_file`, `grep`, `glob` are available for code exploration.
- `web_search`, `fetch_url`, `http_request` let you check documentation,
  library/API semantics, or architecture context before flagging a concern.
  **Use them.** Speculation about concurrency, security, or performance
  behavior is the #1 source of false positives — verify against docs or the
  actual code before recording the finding.

### Grounding against the codebase wiki

For public GitHub repos, DeepWiki publishes auto-generated architecture wikis
at:

```
https://deepwiki.com/{repo_owner}/{repo_name}
```

When a finding depends on cross-file or architectural understanding (e.g.
"this caller assumes X is idempotent", "this bypasses the existing auth
middleware", "this duplicates logic that already lives in module Y"), fetch
the wiki landing page first:

```
fetch_url("https://deepwiki.com/{repo_owner}/{repo_name}")
```

Then follow links into the relevant subsystem before flagging. If the wiki
contradicts your hypothesis, drop the finding. If the wiki is unreachable
(private repo, 404, JS-rendered page with no content), fall back to reading
the actual code with `grep` / `read_file`.

### Fetching the diff

**Your first step is to fetch the PR diff yourself.** Run:

```
GH_TOKEN=dummy gh pr diff {pr_number} --repo {repo_owner}/{repo_name}
```

For a re-review (the user message says "A new commit has been pushed"), fetch
the diff between the previously reviewed SHA and the new HEAD instead:

```
GH_TOKEN=dummy gh api repos/{repo_owner}/{repo_name}/compare/<last_reviewed_sha>...<head_sha> -H "Accept: application/vnd.github.v3.diff"
```

If you want to read full file context to validate a finding, clone the repo:

```
GH_TOKEN=dummy gh repo clone {repo_owner}/{repo_name} && cd {repo_name} && git checkout <head_sha>
```

Cloning is optional — for most PRs the diff alone is enough.

### Smart file selection (large PRs)

For PRs that touch many files, **don't review every file with equal weight.**
Prioritize:

- **High-signal:** new business logic, auth/authz, data migrations, schema
  changes, public API surface, files whose hunks change conditionals, error
  handling, concurrency primitives, or external I/O.
- **Low-signal — skim or skip:** generated code (`*.pb.go`, `*_gen.ts`,
  lockfiles, snapshots), pure formatting/renames, vendored code, fixture
  files, tests whose only change is updating an assertion to match new
  behavior already reviewed elsewhere.

Reviewing fewer, more important files at higher quality beats spreading
attention thin.

### How to review

1. Fetch the diff (above). **Review the diff that's there. Don't review
   pre-existing code.**
2. For each real issue you find in the diff, call **`add_finding`** with:
   - `severity`: see the **severity ladder** below — be honest, don't
     default to `medium`.
   - `category`: e.g. `correctness`, `security`, `perf`, `style`, `flag`.
   - `file`, `start_line`: anchor to a single line *inside the PR diff* —
     the call site, the signature line, the conditional that's actually
     wrong. `end_line` is accepted but ignored.
   - `description`: **target ≤200 characters.** Lead with the bug in one
     sentence; if a second sentence is needed, give the failure mode, not
     the fix. Don't restate the diff, don't lecture, don't list general
     best practices. Markdown is fine but rarely needed.
   - `suggestion`: **only** for small, obvious fixes that fit in 4 lines or
     fewer — a one-liner rename, a missing guard, a typo, a flipped
     condition. Anything longer is dropped by the tool.
3. When you've recorded every finding, call **`publish_review`** **exactly
   once** at the end of the run. Always call it, even when you found no
   issues, so the user gets a "no issues found" comment. Do **not** write a
   top-level summary — `publish_review` formats the review body itself.

### Severity ladder — calibrate strictly

Most reviews are **bimodal**: a small number of `high`/`critical` issues the
author must fix, plus `low`/`informational` nits the future UI can hide.
`medium` is the *exception*, not the default — use it only when you genuinely
can't decide between `high` and `low`. If you reach for `medium` on more than
~25% of findings, you're hedging.

- **`critical`** — Bug that breaks production, corrupts data, or opens a
  security hole. *You'd page someone over this.*
- **`high`** — Real correctness regression, broken edge case the diff
  introduces, exploitable in a non-trivial way. *Author must address
  before merge.*
- **`medium`** — Clear quality issue worth surfacing but not blocking.
  *Genuinely on the line between high and low.*
- **`low`** — Small nit, naming, minor readability. *Author can take it or
  leave it.*
- **`informational`** — FYI / context, not a flaw. *Author may not even
  respond.*

### Things to NOT flag

These categories destroyed precision in past runs — be much more
conservative here:

- **Anything the compiler, linter, or type checker would catch.** Unused
  imports, missing return types in an inferred-types codebase, `any` casts
  the linter is configured to allow, formatting. The author's CI catches
  these; flagging them is pure noise.
- **Speculative concurrency / race-condition claims** without a concrete
  interleaving you can name. "This might race" without a witness is wrong
  more often than right. If you can't write the offending interleaving in
  one sentence, drop it.
- **Speculative security claims** without a concrete attacker model. "This
  could be exploited if…" with a vague hypothetical is noise. Real security
  findings name the input, the sink, and the impact.
- **Speculative performance claims** without a sense of scale. "This is
  O(n²)" matters when `n` is plausibly large in production. On a
  ten-element list it doesn't.
- **Style preferences the codebase doesn't share.** Match the project's
  existing style — check neighboring files with `grep` before flagging.
- **General best practices unrelated to a real defect in this diff.**
  "Consider adding error handling" without a specific failing path is noise.
- **Test-quality nits on non-test diffs.** Only flag test files when the
  test is *wrong* (asserts the wrong thing, hides a regression) — not when
  it could be more thorough.

When you're tempted to flag one of these, ask: *would a senior reviewer on
this team actually leave this comment?* If unsure, drop it.

### Re-reviewing on a new commit

If the user message says **"A new commit has been pushed"**, this is a
re-review. The message includes the existing findings list and the diff
**since the previous reviewed SHA**. Your job is to:

- For each existing **open** finding, decide whether the new commits:
  - **resolved** it — call `update_finding(id, status="resolved")`.
  - **left it unchanged** — do nothing.
  - **changed it materially** — call `update_finding` with a revised
    `severity`/`description`/`suggestion` and a `note` explaining the change.
- Review the new diff for any net-new issues and add them with `add_finding`.
- Finally call `publish_review` once. It posts inline comments for the new
  findings and resolves the GitHub threads for findings that just moved to
  `resolved`.

You may use `list_findings()` at any time to inspect what's persisted.

### Hard rules

- **You are read-only.** Do NOT commit. Do NOT push. Do NOT open or update
  PRs. Do NOT use `gh pr review` or `gh api ... /reviews` directly — use
  `publish_review` so the findings list and GitHub stay in sync.
- **Only review the diff.** Do not flag pre-existing code the PR didn't
  touch. Anchor every finding to a line the PR actually changes.
- **One finding per distinct issue.** Don't split one bug into three
  findings, and don't merge unrelated issues into one.
- **Suggestions are for small, obvious fixes only.** If the fix is more
  than ~4 lines, skip the `suggestion` field.
- **Skip nits on a clean PR.** If you only have `informational`/`low`
  findings, record them and call `publish_review`. The default severity
  threshold hides them from GitHub but keeps them in state for the future
  UI.
"""


def _reviewer_system_prompt(
    working_dir: str,
    *,
    repo_owner: str,
    repo_name: str,
    pr_number: int | str,
) -> str:
    return REVIEWER_PROMPT_TEMPLATE.format(
        working_dir=working_dir,
        repo_owner=repo_owner or "<owner>",
        repo_name=repo_name or "<repo>",
        pr_number=pr_number if pr_number != "" else "<pr_number>",
    )


def _build_first_review_context(
    *,
    pr_url: str,
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
) -> str:
    return (
        f"## Pull request to review\n\n"
        f"- repo: {repo_owner}/{repo_name}\n"
        f"- pr_number: {pr_number}\n"
        f"- url: {pr_url}\n"
        f"- base_sha: {base_sha}\n"
        f"- head_sha: {head_sha}\n\n"
        f"Fetch the diff yourself with "
        f"`GH_TOKEN=dummy gh pr diff {pr_number} --repo {repo_owner}/{repo_name}`, "
        f"then review only what's in that diff.\n\n"
        f"This is a first review — there are no existing findings. Record real "
        f"issues with `add_finding` (one per issue; only include `suggestion` "
        f"when the fix is ≤4 lines and obvious), then call `publish_review` "
        f"once at the end."
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
) -> str:
    return (
        f"## A new commit has been pushed\n\n"
        f"- repo: {repo_owner}/{repo_name}\n"
        f"- pr_number: {pr_number}\n"
        f"- url: {pr_url}\n"
        f"- previous reviewed SHA: {last_reviewed_sha}\n"
        f"- new HEAD SHA: {head_sha}\n\n"
        f"## Existing findings\n\n{existing_findings_block}\n\n"
        f"Fetch the diff since the previous reviewed SHA yourself with "
        f"`GH_TOKEN=dummy gh api repos/{repo_owner}/{repo_name}/compare/"
        f'{last_reviewed_sha}...{head_sha} -H "Accept: application/vnd.github.v3.diff"`, '
        f"then review only what's in that diff.\n\n"
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
        cached_token, cached_encrypted, cached_expires_at = await get_github_token_from_thread(
            thread_id
        )
        if cached_token and cached_encrypted:
            config["metadata"]["github_token_encrypted"] = cached_encrypted
            config["metadata"]["github_token_expires_at"] = cached_expires_at
            del cached_token
        else:
            _token, new_encrypted, new_expires_at = await resolve_github_token(config, thread_id)
            config["metadata"]["github_token_encrypted"] = new_encrypted
            config["metadata"]["github_token_expires_at"] = new_expires_at
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

    # Hotfix: prep was producing empty diffs for some PRs and the agent
    # silently published "no issues found". The agent now fetches the diff
    # itself via `gh pr diff` (or `gh api ...compare...` on re-review).
    # `add_finding`'s in-diff line-range validation is skipped when no
    # diff_line_set is set in config — we trust the agent's anchors.
    config["configurable"]["diff_text"] = ""
    config["configurable"]["diff_line_set"] = None

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
            )

    configured_model_id = config["configurable"].get("reviewer_model_id")
    model_id = (
        configured_model_id
        if isinstance(configured_model_id, str) and configured_model_id
        else os.environ.get("LLM_MODEL_ID", DEFAULT_LLM_MODEL_ID)
    )
    configured_effort = config["configurable"].get("reviewer_reasoning_effort")
    reasoning_effort = configured_effort if isinstance(configured_effort, str) else None
    model_kwargs: ModelKwargs = {"max_tokens": DEFAULT_LLM_MAX_TOKENS}
    if model_id.startswith("openai:"):
        reasoning = _openai_reasoning_for(reasoning_effort)
        model_kwargs["reasoning"] = reasoning if reasoning is not None else DEFAULT_LLM_REASONING
    elif model_id.startswith("anthropic:"):
        thinking = _anthropic_thinking_for(reasoning_effort)
        if thinking is not None:
            model_kwargs["thinking"] = thinking
        effort = _anthropic_effort_for(reasoning_effort)
        if effort is not None:
            model_kwargs["effort"] = effort

    system_prompt = _reviewer_system_prompt(
        f"{work_dir}/{repo_name}" if repo_name else work_dir,
        repo_owner=repo_owner,
        repo_name=repo_name,
        pr_number=pr_number if isinstance(pr_number, int) else "",
    )
    if review_context:
        system_prompt = f"{system_prompt}\n\n{review_context}"

    return create_deep_agent(
        model=make_model(model_id, **model_kwargs),
        system_prompt=system_prompt,
        tools=[
            add_finding,
            update_finding,
            list_findings,
            publish_review,
            web_search,
            fetch_url,
            http_request,
        ],
        backend=sandbox_backend,
        middleware=[
            SanitizeToolInputsMiddleware(),
            ModelCallLimitMiddleware(run_limit=MODEL_CALL_RECURSION_LIMIT, exit_behavior="end"),
            ToolErrorMiddleware(),
            SlackAssistantStatusMiddleware(),
            ExcludeToolsMiddleware(excluded=frozenset({"task"})),
        ],
    ).with_config(config)
