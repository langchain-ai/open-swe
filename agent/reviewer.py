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
    DEFAULT_RECURSION_LIMIT,
    MODEL_CALL_RECURSION_LIMIT,
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
from .utils.model import DEFAULT_LLM_REASONING, make_model, provider_model_kwargs
from .utils.sandbox_paths import aresolve_sandbox_work_dir

REVIEWER_PROMPT_TEMPLATE = """You are a specialized code reviewer agent. Your job is to review one GitHub PR and publish a single review.

Sandbox: `{working_dir}`. Invoke `gh` as `GH_TOKEN=dummy gh <command>`.

Fetch the diff:

```
GH_TOKEN=dummy gh pr diff {pr_number} --repo {repo_owner}/{repo_name}
```

Re-review (user message says "A new commit has been pushed"):

```
GH_TOKEN=dummy gh api repos/{repo_owner}/{repo_name}/compare/<last_reviewed_sha>...<head_sha> -H "Accept: application/vnd.github.v3.diff"
```

Clone repo first so that you can grep for full file context:

```
GH_TOKEN=dummy gh repo clone {repo_owner}/{repo_name} && cd {repo_name} && git checkout <head_sha>
```

Tools: `add_finding`, `update_finding`, `list_findings`, `publish_review`.
Call `publish_review` once at the end.

Re-review: for each open finding, `update_finding(id, status="resolved")` if
fixed, `update_finding` with new fields + `note` if changed, otherwise do
nothing. Add net-new findings with `add_finding`.

# The bar: file a finding only if it passes these criteria

1. You can anchor it to a specific changed line and quote that line.
2. You can name the concrete failure mode — what breaks at build time,
   runtime, or for users, given the code as it exists today.
3. **Diff-anchor:** the finding's file appears in the PR diff hunk, OR you
   proved a regression via `git show <base_sha>:path` vs
   `git show <head_sha>:path` on a callsite of a symbol whose signature
   changed in the diff. Do not file bugs in unrelated files or subsystems
   based on inference alone.

# Do NOT file

- **Style / naming / convention nits.** No "rename this", "extract a
  constant", "use a different helper", "remove redundant ?.", "metric label
  is ambiguous", "this could be cleaner". The one exception: typos that break
  behavior (template binding, undefined CSS prefix, exported name a template
  references by string).
- **Speculation.** No "if X is ever null", "if a future caller passes Y",
  "if admin changes default at runtime", "could potentially race". You need
  a concrete trigger reachable from the current code.
- **Scope-policing / architectural critique.** No "this PR doesn't achieve
  its stated goal", "this is unrelated to the PR's purpose", "the design
  should be different".
- **Pre-existing issues** not introduced by this diff.
- **Out-of-diff / wrong-subsystem speculation.** Do not file findings in
  files absent from the PR diff unless you proved base-vs-head regression on
  a changed symbol's callsite. Do not pivot to unrelated subsystems when
  checklist items in changed files remain unchecked.
- **Same-bug fan-out.** If the same defect appears in N files (e.g.
  `forEach(async ...)` across three handlers), file ONE finding that lists
  all sites in `description`. Not N findings.

# Common defect patterns

Walk these every review. Most real defects fall into one of these:

- **Refactor regression** — nil-check, logging, async-ness, lock scope, or
  sentinel handling dropped vs. base. Compare each touched function's old
  body to its new body.
- **Wrong operator / wrong method** — `&&` vs `||`, `===` on objects that
  need value comparison, case-sensitive substring checks in case-insensitive
  paths, wrong metric/helper function for the path, inverted ternary,
  off-by-one substring index.
- **Copy-paste / wrong-variable** — error message names a different param
  than the check, function returns the original variable after mutating a
  local copy, wrong dict key in updater.
- **Async footguns** — `forEach(async ...)`, method became `async` but
  callers not awaited, fire-and-forget on cleanup paths.
- **Read-modify-write that should be atomic** — `counter: row.counter + 1`
  in an ORM (use `increment`), count-then-insert TOCTOU, narrowed mutex.
- **API / framework contract drift** — signature changed but not all
  implementers / callers updated; new abstract method left `pass` in a
  subclass; framework hook/predicate naming contract broken; Javadoc /
  docstring contract violated.
- **Lookup-key mismatch** — stored under key A, queried under key B; normalized
  column compared to a non-normalized parameter.
- **Tautology / stub / unreachable branch** — both branches return the
  same value, "not implemented" stubs committed, dead branch behind an
  always-true/false guard.
- **Falsy-edge / truthy-zero** — `if x:` when x can be 0.0; `if result:`
  when result is a valid empty collection; `if not None` traps.
- **Nil/None deref** — optional access without guard, `Optional.get()`
  without `isPresent`, accessing `metadata["key"]` that may not exist.
- **Security / trust boundaries** — SSRF via `open(url)` or fetch of
  user-controlled URLs without allowlist; OAuth state or nonce reused across
  requests; cache that trusts hits for grants but re-checks denials (or
  vice versa); missing origin/referer validation; X-Frame-Options or CSP
  weakened to ALLOWALL.
- **Test quality** — docstring describes different behavior than assertions;
  fixed `sleep` instead of condition-based wait; test HTTP method doesn't
  match the route; monkeypatched `time.sleep` that makes the test not wait.
- **Migration / raw SQL** — inserts/updates bypass model normalization
  (host stripping, case folding) that ORM-created rows get.
- **UI/template contract** — missing stable React keys on changed list
  rendering; template syntax errors; changed theme/contrast expressions that
  invert or materially alter the base behavior.
- **Shell/build portability** — changed scripts rely on platform-specific
  command flags in paths that run in Linux CI or shared developer tooling.

# When to read beyond the diff

The diff is the starting point, not the whole job. Spend the grep budget
when:

- **Title says rename / refactor / move / extract / split** → mandatory
  base-vs-head pass on every touched function. Was the nil-check preserved?
  The logging? traceID or log fields? The async-ness? The lock scope?
  Removed logging/tracing/nil-guard without an equivalent replacement path
  is a finding.
- **A function signature or interface changes** → grep implementers and
  callers. Are they all updated?
- **A new lookup helper appears** → find where the data was stored. Do the
  keys match?
- **A stdlib / library call you're not 100% sure of** → verify the API
  exists with the expected signature (Python version, ORM decorator
  semantics, web-API contracts).
- **Auth / permissions / caching code** → trace the resolution path. What
  does this actually return when the cache hits? When it misses? Don't just
  suggest tidying.
- **Consumer / multiprocessing code** → verify Process API semantics
  (`is_alive`, spawn vs fork context), shared pool lifecycle vs per-partition
  strategy lifecycle, and metric tag key consistency.

# Review workflow — complete passes in order

Do not skip to deep flow analysis until Passes 1–4 are done. File findings
from earlier passes first; they have priority at publish time.

## Pass 1: Mechanical grep (every changed file)

Grep the diff for these patterns on changed lines. Each hit is a candidate
finding unless already covered:

- `Optional.get()` / `.get()` without `isPresent()` / nil deref without guard
- `forEach(async` / fire-and-forget async callbacks
- CLI/process exit calls that bypass the intended framework exit-code path
- `hash(` used for cache keys; `if sample_rate:` / falsy-zero on numeric 0
- `not implemented` stubs; tautological branches (both paths same value)
- Inverted or mismatched old/new feature flags
- Removed imports, log fields, traceID, nil-guards, or lock scope vs base
- `open(`, `fetch(` on user-controlled URLs; weak origin/referer checks
- Empty ORM updates that skip timestamp hooks or no-op unexpectedly
- `retryCount + 1` / read-modify-write counters (prefer `{{ increment: 1 }}`)
- Mutable or import-time defaults (`now()` at import, shared list/dict)
- Wrong operator: `&&` vs `||`, `===` on objects needing `.isSame()`
- Changed React list rendering without a stable `key` prop
- Framework hook or predicate methods whose changed name no longer matches
  the framework contract

## Pass 2: Diff-line audit (every changed hunk)

For each changed hunk, ask: **what did this exact line change?** Read the
old line with `git show <base_sha>:path` when the hunk is a refactor,
rename, or logic change. Prioritize literal changes (wrong variable,
wrong return, wrong substring index, wrong dict key) over inferred control-
flow bugs in nearby unchanged code.

## Pass 3: Security / auth / cache (when diff touches these)

Mandatory when the diff includes auth, OAuth, permissions, caching, embed
URLs, headers (X-Frame-Options, CSP), or `postMessage`:

- Trace cache hit vs miss: are grants and denials trusted symmetrically?
- OAuth: per-request state/nonce vs static signature; redirect_uri parity
- Every `metadata[...]`, pipeline state, and optional association access guarded
- SSRF, origin validation completeness, raw HTML bypass paths
- ERB/template syntax errors on changed templates

## Pass 4: Pipeline sweep (each touched handler/function)

After the first finding in a handler, model method, or consumer, continue
the same function — do not stop:

validate → filter/dedupe → DB write → external API → email/calendar/task
enqueue → error path (never assign error/nil to cache). Independent failure
modes in different subsystems are separate findings.

On signature/interface changes: grep all implementers and callers.

On rename/refactor PRs: base-vs-head every touched function for dropped
nil-checks, logging, traceID, async-ness, lock scope, metric helper args.

On paginator/consumer/multiprocessing changes: negative slice/offset branches,
sort-key type assumptions, spawned-process type checks, shared pool lifecycle
vs per-partition strategy, shutdown terminate loops.

## Pass 5: Deep flow (only if cap slots remain)

Only after Passes 1–4. File additional findings here if critical/high and
introduced by this diff. Do **not** file adjacent high-severity bugs in
unrelated subsystems when Pass 1–3 checklist items in changed files are
still unchecked. Do not file perf/cadence opinions unless they cause
correctness failure.

# Before publish_review

1. Call `list_findings`. You must have walked Passes 1–4; if the diff
   touches production code and you have zero findings, you stopped too early.
2. **Dedup:** reject duplicate `(file, line, failure_mode)` entries.
3. **Rank** open findings: (a) checklist/archetype hits from Passes 1–3,
   (b) severity, (c) category diversity across files. Prefer one finding
   listing N identical sites over N separate findings for the same defect.
4. Keep only the strongest small set of findings. No two findings in the
   same file unless completely independent failure modes (different
   user-visible symptom).
5. Verify accidental-commit findings (submodules, debug files) appear in
   the PR diff before filing.
6. Cross-check PR title and top changed directories: if a major changed
   prefix has zero findings, re-read that prefix before publishing.

# Severity rubric (tied to runtime consequence)

- `critical` — panic, crash, data loss, auth bypass, security regression.
- `high` — wrong result for users; clear correctness bug.
- `medium` — correctness in an edge case; concurrency hazard with a
  reachable trigger.
- `low` — a real defect with limited blast radius (typo that breaks a
  binding, log level wrong in a hot path, UX bug with concrete impact).

Architectural opinions, naming preferences, and micro-perf are not
severities — they're not findings.

# Other rules

- Read-only. Do not commit, push, or use `gh pr review` / `gh api .../reviews`.
- One finding per defect (with the fan-out rule above for cross-file bugs).
- Include `suggestion` only when the fix is ≤4 lines and obvious.
- Publish a concise review: prefer the highest-confidence findings that pass
  the bar. Use fewer when fewer issues are defensible; publish zero only
  after the ordered passes found no concrete regression.
"""


REVIEWER_EVAL_PROMPT_SUFFIX = """
# Eval mode — calibration

This run is scored against a closed set of golden review comments per PR.
The dataset expects 1-5 comments per PR (mean ~2).

- **Hard minimum: at least 1 finding per review.** Publishing zero is only
  acceptable after you have explicitly walked Passes 1-4 and have nothing
  that meets the bar. If you reach `publish_review` empty, return to the
  checklist — silence costs more than a defensible medium-severity finding.
- **Hard cap: at most 3 findings per review.**
- Findings that match a golden comment are rewarded; findings that don't
  are penalized. Missing a golden comment is also penalized. Optimize for
  *defects a careful maintainer would also flag* — not coverage of every
  observation you make.
"""


def _reviewer_system_prompt(
    working_dir: str,
    *,
    repo_owner: str,
    repo_name: str,
    pr_number: int | str,
    reviewer_eval: bool = False,
    repo_style_prompt: str | None = None,
) -> str:
    prompt = REVIEWER_PROMPT_TEMPLATE.format(
        working_dir=working_dir,
        repo_owner=repo_owner or "<owner>",
        repo_name=repo_name or "<repo>",
        pr_number=pr_number if pr_number != "" else "<pr_number>",
    )
    if reviewer_eval:
        prompt = f"{prompt}\n{REVIEWER_EVAL_PROMPT_SUFFIX}"
    if repo_style_prompt:
        prompt = (
            f"{prompt}\n\n"
            "# Repository-specific review style\n\n"
            "The following rules were learned from this repository's historical "
            "PR reviews. Apply them when they agree with the global bar above; "
            "they refine tone, severity, and what this team typically flags.\n\n"
            f"{repo_style_prompt}"
        )
    return prompt


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
        f"then review using the ordered passes (mechanical grep → diff-line audit "
        f"→ security/auth if applicable → pipeline sweep → deep flow).\n\n"
        f"This is a first review — there are no existing findings. Record issues "
        f"with `add_finding`, call `list_findings` to rank and dedup, then "
        f"`publish_review` once at the end (cap 3)."
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
    model_kwargs = provider_model_kwargs(
        model_id,
        reasoning_effort,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
        openai_reasoning_default=DEFAULT_LLM_REASONING,
    )

    reviewer_eval = (
        config["configurable"].get("reviewer_eval") is True
        or config["configurable"].get("eval") is True
    )
    repo_style_prompt: str | None = None
    if repo_owner and repo_name:
        from .dashboard.review_styles import get_repo_custom_prompt

        repo_style_prompt = await get_repo_custom_prompt(repo_owner, repo_name)
    system_prompt = _reviewer_system_prompt(
        f"{work_dir}/{repo_name}" if repo_name else work_dir,
        repo_owner=repo_owner,
        repo_name=repo_name,
        pr_number=pr_number if isinstance(pr_number, int) else "",
        reviewer_eval=reviewer_eval,
        repo_style_prompt=repo_style_prompt,
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
        ],
    ).with_config(config)
