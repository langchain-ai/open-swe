"""Reviewer-only tools for resolving PRs to author coding-agent traces."""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from langgraph.config import get_config

from ..dashboard.team_credentials import get_langsmith_credentials
from ..dashboard.team_settings import get_team_review_tracing_project
from ..integrations.langsmith_tools import _client
from ..reviewer_findings import get_thread_id_from_runtime, get_thread_metadata, get_thread_pr_meta
from ..utils.github_http import GITHUB_API_BASE, github_client, github_request
from ..utils.github_token import get_github_token
from ..utils.langsmith import get_langsmith_trace_url

_STRONG_MATCH_THRESHOLD = 0.70
_MAX_SEARCH_RESULTS = 50
_MAX_COMMITS = 25
_MAX_CHANGED_FILES = 30
_MAX_SESSION_RUNS = 100
_MAX_RUN_TEXT_CHARS = 4_000
_MAX_DIGEST_SNIPPETS = 5
_GENERIC_BRANCHES = {
    "main",
    "master",
    "develop",
    "development",
    "dev",
    "staging",
    "stage",
    "prod",
    "production",
    "release",
    "trunk",
}
_PR_URL_RE = re.compile(r"^https://github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)([\s:=]+)([^\s'\"`]+)"
)
_PATH_RE = re.compile(
    r"(?<![\w./-])([\w./-]+\.(?:py|ts|tsx|js|jsx|json|md|yml|yaml|toml|lock|go|rs|java|kt|swift|rb|php|css|scss|html|sql))(?![\w./-])"
)


@dataclass
class _PRContext:
    owner: str
    repo: str
    pr_number: int
    pr_url: str
    branch_name: str = ""
    head_sha: str = ""
    base_sha: str = ""
    author: str = ""
    created_at: str = ""
    commit_shas: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Candidate:
    thread_id: str
    score: float = 0.0
    has_strong_key: bool = False
    evidence: set[str] = field(default_factory=set)
    repo: str | None = None
    matching_files: set[str] = field(default_factory=set)
    run_ids: set[str] = field(default_factory=set)
    first_turn: str | None = None
    last_turn: str | None = None
    turn_count: int = 0


def resolve_pr_to_threads(pr_url: str | None = None) -> dict[str, Any]:
    """Resolve the current PR to likely author trace thread(s)."""
    try:
        return asyncio.run(_resolve_pr_to_threads_async(pr_url=pr_url))
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}", "candidates": []}


async def _resolve_pr_to_threads_async(pr_url: str | None = None) -> dict[str, Any]:
    project = await get_team_review_tracing_project()
    if project is None:
        return {
            "success": False,
            "reason": "not_configured",
            "error": "No review_tracing_project is configured for this team.",
            "candidates": [],
        }

    creds = await get_langsmith_credentials()
    if creds is None:
        return {
            "success": False,
            "reason": "langsmith_not_connected",
            "error": "LangSmith credentials are not connected for this team.",
            "candidates": [],
        }

    context = await _build_pr_context(pr_url)
    if context is None:
        return {
            "success": False,
            "reason": "missing_pr_context",
            "error": "Missing repo or PR info in the reviewer run config.",
            "candidates": [],
        }

    client = _client(creds)
    candidates: dict[str, _Candidate] = {}

    async def apply_hits(label: str, weight: float, query: str, *, strong: bool) -> None:
        runs = await _search_runs(client, project, query, limit=_MAX_SEARCH_RESULTS)
        for run in runs:
            thread_id = _run_thread_id(run)
            if not thread_id:
                continue
            candidate = candidates.setdefault(thread_id, _Candidate(thread_id=thread_id))
            candidate.score = max(candidate.score, weight)
            candidate.has_strong_key = candidate.has_strong_key or strong
            candidate.evidence.add(label)
            _record_run(candidate, run)

    if _is_specific_branch(context.branch_name):
        await apply_hits(f"branch:{context.branch_name}", 0.55, context.branch_name, strong=True)
    elif context.branch_name:
        context.warnings.append(
            f"Skipped generic branch name as a strong key: {context.branch_name}"
        )

    for sha in _strong_sha_queries(context):
        await apply_hits(f"sha:{sha[:10]}", 0.78, sha, strong=True)
        if len(sha) > 10:
            await apply_hits(f"sha_prefix:{sha[:10]}", 0.70, sha[:10], strong=True)

    pr_path = f"{context.owner}/{context.repo}/pull/{context.pr_number}"
    await apply_hits(f"pr_url:{pr_path}", 0.80, pr_path, strong=True)

    if not candidates:
        return {
            "success": True,
            "project": project,
            "candidates": [],
            "low_confidence_candidates": [],
            "warnings": context.warnings,
        }

    await _apply_repo_evidence(client, project, candidates, context)
    await _apply_file_evidence(client, project, candidates, context)
    await _augment_candidate_sessions(client, project, candidates, context)

    ranked = sorted(candidates.values(), key=lambda c: c.score, reverse=True)
    qualified = [c for c in ranked if c.has_strong_key and c.score >= _STRONG_MATCH_THRESHOLD]
    qualified_ids = {c.thread_id for c in qualified}
    low_confidence = [c for c in ranked if c.thread_id not in qualified_ids][:3]
    return {
        "success": True,
        "project": project,
        "candidates": [_candidate_dict(c, project) for c in qualified],
        "low_confidence_candidates": [_candidate_dict(c, project) for c in low_confidence],
        "warnings": context.warnings,
    }


def summarize_agent_session(thread_id: str) -> dict[str, Any]:
    """Return a compact digest for a resolved author trace thread."""
    if not isinstance(thread_id, str) or not thread_id.strip():
        return {"success": False, "error": "thread_id is required"}
    try:
        return asyncio.run(_summarize_agent_session_async(thread_id.strip()))
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


async def _summarize_agent_session_async(thread_id: str) -> dict[str, Any]:
    project = await get_team_review_tracing_project()
    if project is None:
        return {
            "success": False,
            "reason": "not_configured",
            "error": "No tracing project configured",
        }
    creds = await get_langsmith_credentials()
    if creds is None:
        return {
            "success": False,
            "reason": "langsmith_not_connected",
            "error": "LangSmith credentials are not connected for this team.",
        }

    client = _client(creds)
    runs = await _list_thread_runs(client, project, thread_id, limit=_MAX_SESSION_RUNS)
    if not runs:
        return {"success": False, "reason": "not_found", "error": "No runs found for thread"}

    runs.sort(key=lambda r: _run_time(r, "start_time") or datetime.min.replace(tzinfo=UTC))
    texts = [_run_text(run) for run in runs]
    combined = "\n".join(text for text in texts if text)
    files = sorted(_extract_files(combined))[:50]

    reasoning = _snippets(combined, ("because", "therefore", "decided", "implemented", "fixed"))
    if not reasoning:
        reasoning = _snippets(combined, ("summary", "plan", "approach"))
    alternatives = _snippets(
        combined, ("alternative", "option", "instead", "considered", "approach")
    )
    dismissed = _snippets(
        combined,
        ("dismiss", "not an issue", "acceptable", "safe because", "not needed", "won't"),
    )
    edge_cases = _snippets(
        combined,
        ("edge case", "empty", "none", "null", "error", "fallback", "timeout", "race", "auth"),
    )

    first = _run_time(runs[0], "start_time")
    last = _run_time(runs[-1], "end_time") or _run_time(runs[-1], "start_time")
    return {
        "success": True,
        "thread_id": thread_id,
        "trace_url": _trace_url(thread_id, project),
        "turn_count": len(runs),
        "first_turn": first.isoformat() if first else None,
        "last_turn": last.isoformat() if last else None,
        "digest": {
            "author_reasoning": " ".join(reasoning[:2]) if reasoning else "",
            "alternatives_considered": alternatives,
            "concerns_dismissed": dismissed,
            "edge_cases_handled": edge_cases,
            "files_touched": files,
        },
    }


async def _build_pr_context(pr_url: str | None) -> _PRContext | None:
    config = get_config()
    raw_configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    configurable = raw_configurable if isinstance(raw_configurable, dict) else {}
    repo_config = configurable.get("repo")
    pr_number = configurable.get("pr_number")
    configured_url = configurable.get("pr_url")

    owner = repo_config.get("owner") if isinstance(repo_config, dict) else None
    repo = repo_config.get("name") if isinstance(repo_config, dict) else None
    url_source = pr_url if pr_url is not None else configured_url
    url = url_source if isinstance(url_source, str) else ""
    if pr_url:
        match = _PR_URL_RE.match(pr_url.strip())
        if not match:
            return None
        owner, repo, number = match.groups()
        pr_number = int(number)
        url = pr_url.strip()

    if not isinstance(owner, str) or not owner or not isinstance(repo, str) or not repo:
        return None
    if not isinstance(pr_number, int):
        return None

    context = _PRContext(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        pr_url=url or f"https://github.com/{owner}/{repo}/pull/{pr_number}",
        branch_name=str(configurable.get("branch_name", "") or ""),
        head_sha=str(configurable.get("head_sha", "") or ""),
        base_sha=str(configurable.get("base_sha", "") or ""),
        author=str(configurable.get("github_login", "") or ""),
    )

    thread_id = get_thread_id_from_runtime()
    if thread_id:
        try:
            metadata = await get_thread_metadata(thread_id)
            pr_meta = get_thread_pr_meta(metadata)
            if pr_meta:
                context.author = str(pr_meta.get("author") or context.author)
                context.branch_name = str(pr_meta.get("head_ref") or context.branch_name)
        except Exception:
            context.warnings.append("Could not read reviewer thread metadata")

    token = get_github_token()
    if not token:
        context.warnings.append("No GitHub token available; using config-only PR context")
        if context.head_sha:
            context.commit_shas = [context.head_sha]
        return context

    await _hydrate_pr_context_from_github(context, token)
    if context.head_sha and context.head_sha not in context.commit_shas:
        context.commit_shas.insert(0, context.head_sha)
    return context


async def _hydrate_pr_context_from_github(context: _PRContext, token: str) -> None:
    async with github_client(token=token) as client:
        pull_url = (
            f"{GITHUB_API_BASE}/repos/{context.owner}/{context.repo}/pulls/{context.pr_number}"
        )
        try:
            response = await github_request(client, "GET", pull_url)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                head = data.get("head") if isinstance(data.get("head"), dict) else {}
                user = data.get("user") if isinstance(data.get("user"), dict) else {}
                context.branch_name = str(head.get("ref") or context.branch_name)
                context.head_sha = str(head.get("sha") or context.head_sha)
                base = data.get("base") if isinstance(data.get("base"), dict) else {}
                context.base_sha = str(base.get("sha") or context.base_sha)
                context.author = str(user.get("login") or context.author)
                context.created_at = str(data.get("created_at") or "")
        except httpx.HTTPError as exc:
            context.warnings.append(f"Could not fetch PR metadata: {exc}")

        context.commit_shas = await _fetch_commit_shas(client, context)
        context.changed_files = await _fetch_changed_files(client, context)


async def _fetch_commit_shas(client: httpx.AsyncClient, context: _PRContext) -> list[str]:
    out: list[str] = []
    page = 1
    while len(out) < _MAX_COMMITS:
        url = (
            f"{GITHUB_API_BASE}/repos/{context.owner}/{context.repo}/pulls/{context.pr_number}"
            f"/commits?per_page=100&page={page}"
        )
        try:
            response = await github_request(client, "GET", url)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            context.warnings.append(f"Could not fetch PR commits: {exc}")
            break
        if not isinstance(data, list) or not data:
            break
        for item in data:
            sha = item.get("sha") if isinstance(item, dict) else None
            if isinstance(sha, str) and sha and sha not in out:
                out.append(sha)
                if len(out) >= _MAX_COMMITS:
                    break
        if len(data) < 100:
            break
        page += 1
    return out


async def _fetch_changed_files(client: httpx.AsyncClient, context: _PRContext) -> list[str]:
    out: list[str] = []
    page = 1
    while len(out) < _MAX_CHANGED_FILES:
        url = (
            f"{GITHUB_API_BASE}/repos/{context.owner}/{context.repo}/pulls/{context.pr_number}"
            f"/files?per_page=100&page={page}"
        )
        try:
            response = await github_request(client, "GET", url)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            context.warnings.append(f"Could not fetch PR files: {exc}")
            break
        if not isinstance(data, list) or not data:
            break
        for item in data:
            filename = item.get("filename") if isinstance(item, dict) else None
            if isinstance(filename, str) and filename and filename not in out:
                out.append(filename)
                if len(out) >= _MAX_CHANGED_FILES:
                    break
        if len(data) < 100:
            break
        page += 1
    return out


def _strong_sha_queries(context: _PRContext) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for sha in [context.head_sha, *context.commit_shas]:
        if not isinstance(sha, str):
            continue
        normalized = sha.strip()
        if len(normalized) < 10 or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
        if len(out) >= _MAX_COMMITS:
            break
    return out


async def _apply_repo_evidence(
    client: Any, project: str, candidates: dict[str, _Candidate], context: _PRContext
) -> None:
    repo_full = f"{context.owner}/{context.repo}"
    repo_runs = await _search_runs(client, project, repo_full, limit=_MAX_SEARCH_RESULTS)
    metadata_runs = await _list_runs(
        client,
        project,
        _metadata_has_filter("repository_name", repo_full),
        limit=_MAX_SEARCH_RESULTS,
    )
    for run in [*repo_runs, *metadata_runs]:
        thread_id = _run_thread_id(run)
        if not thread_id or thread_id not in candidates:
            continue
        candidate = candidates[thread_id]
        candidate.repo = repo_full
        _add_bonus(candidate, f"repo:{repo_full}", 0.15)
        _record_run(candidate, run)


async def _apply_file_evidence(
    client: Any, project: str, candidates: dict[str, _Candidate], context: _PRContext
) -> None:
    for file_path in context.changed_files[:_MAX_CHANGED_FILES]:
        if len(file_path) < 4:
            continue
        runs = await _search_runs(client, project, file_path, limit=_MAX_SEARCH_RESULTS)
        for run in runs:
            thread_id = _run_thread_id(run)
            if not thread_id or thread_id not in candidates:
                continue
            candidate = candidates[thread_id]
            if file_path not in candidate.matching_files:
                candidate.matching_files.add(file_path)
                _add_bonus(candidate, f"file:{file_path}", 0.02)
            _record_run(candidate, run)


async def _augment_candidate_sessions(
    client: Any, project: str, candidates: dict[str, _Candidate], context: _PRContext
) -> None:
    created_at = _parse_time(context.created_at)
    for candidate in candidates.values():
        runs = await _list_thread_runs(
            client, project, candidate.thread_id, limit=_MAX_SESSION_RUNS
        )
        if not runs:
            continue
        candidate.turn_count = len(runs)
        for run in runs:
            _record_run(candidate, run)
            metadata = _run_metadata(run)
            repo_name = metadata.get("repository_name")
            repo_full = f"{context.owner}/{context.repo}"
            if repo_name == repo_full:
                candidate.repo = repo_full
                _add_bonus(candidate, f"repo:{repo_full}", 0.05)
            if _author_matches(metadata, context.author):
                _add_bonus(candidate, f"author:{context.author}", 0.05)
        if created_at and candidate.last_turn:
            last = _parse_time(candidate.last_turn)
            if last and last <= created_at:
                delta = created_at - last
                if delta.days <= 14:
                    _add_bonus(candidate, "time:before_pr_creation", 0.05)


def _add_bonus(candidate: _Candidate, evidence: str, weight: float) -> None:
    if evidence in candidate.evidence:
        return
    candidate.evidence.add(evidence)
    candidate.score = min(candidate.score + weight, 1.0)


def _record_run(candidate: _Candidate, run: Any) -> None:
    run_id = _run_id(run)
    if run_id:
        candidate.run_ids.add(run_id)
    start = _run_time(run, "start_time")
    end = _run_time(run, "end_time") or start
    first = _parse_time(candidate.first_turn)
    last = _parse_time(candidate.last_turn)
    if start and (first is None or start < first):
        candidate.first_turn = start.isoformat()
    if end and (last is None or end > last):
        candidate.last_turn = end.isoformat()
    if candidate.turn_count == 0:
        candidate.turn_count = len(candidate.run_ids)


def _candidate_dict(candidate: _Candidate, project: str) -> dict[str, Any]:
    return {
        "thread_id": candidate.thread_id,
        "confidence": round(min(candidate.score, 1.0), 2),
        "evidence": sorted(candidate.evidence),
        "repo": candidate.repo,
        "turn_count": candidate.turn_count,
        "first_turn": candidate.first_turn,
        "last_turn": candidate.last_turn,
        "trace_url": _trace_url(candidate.thread_id, project),
    }


async def _search_runs(client: Any, project: str, query: str, *, limit: int) -> list[Any]:
    query = query.strip()
    if len(query) < 3:
        return []
    return await _list_runs(client, project, f'search("{_filter_string(query)}")', limit=limit)


async def _list_thread_runs(client: Any, project: str, thread_id: str, *, limit: int) -> list[Any]:
    return await _list_runs(
        client,
        project,
        _metadata_has_filter("thread_id", thread_id),
        limit=limit,
    )


async def _list_runs(client: Any, project: str, filter_expr: str, *, limit: int) -> list[Any]:
    capped = max(1, min(limit, _MAX_SESSION_RUNS))

    def _call() -> list[Any]:
        kwargs: dict[str, Any] = {"filter": filter_expr, "limit": capped}
        if _looks_uuid(project):
            kwargs["project_id"] = project
        else:
            kwargs["project_name"] = project
        try:
            return list(client.list_runs(**kwargs))
        except TypeError:
            kwargs.pop("project_id", None)
            kwargs["project_name"] = project
            return list(client.list_runs(**kwargs))

    return await asyncio.to_thread(_call)


def _metadata_has_filter(key: str, value: str) -> str:
    return f"has(metadata, '{json.dumps({key: value}, separators=(',', ':'))}')"


def _filter_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _run_thread_id(run: Any) -> str | None:
    metadata = _run_metadata(run)
    value = metadata.get("thread_id")
    return value if isinstance(value, str) and value else None


def _run_metadata(run: Any) -> dict[str, Any]:
    metadata = _get(run, "metadata")
    if isinstance(metadata, dict):
        return metadata
    extra = _get(run, "extra")
    if isinstance(extra, dict) and isinstance(extra.get("metadata"), dict):
        return extra["metadata"]
    return {}


def _run_id(run: Any) -> str | None:
    value = _get(run, "id")
    return str(value) if value is not None else None


def _run_time(run: Any, field_name: str) -> datetime | None:
    return _parse_time(_get(run, field_name))


def _get(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _is_specific_branch(branch: str) -> bool:
    normalized = branch.strip().lower()
    if len(normalized) < 3:
        return False
    if normalized.startswith(("refs/heads/", "origin/")):
        normalized = normalized.rsplit("/", 1)[-1]
    return normalized not in _GENERIC_BRANCHES


def _author_matches(metadata: dict[str, Any], author: str) -> bool:
    if not author:
        return False
    expected = author.lower()
    for key in ("local_username", "anthropic_user_id", "user", "username"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            lower = value.lower()
            if lower == expected or expected in lower:
                return True
    return False


def _run_text(run: Any) -> str:
    chunks: list[str] = []
    for name in ("inputs", "outputs"):
        value = _get(run, name)
        if value is None:
            continue
        text = _stringify_limited(value, _MAX_RUN_TEXT_CHARS)
        if text:
            chunks.append(text)
    return _redact("\n".join(chunks))


def _stringify_limited(value: Any, limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    return text[:limit]


def _extract_files(text: str) -> set[str]:
    out: set[str] = set()
    for match in _PATH_RE.finditer(text):
        path = match.group(1).strip(".,;:()[]{}'\"")
        if "://" in path or path.startswith(("http", "www.")):
            continue
        out.add(path)
    return out


def _snippets(text: str, keywords: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    lines = re.split(r"[\n\r]+|(?<=[.!?])\s+", text)
    for raw in lines:
        line = " ".join(raw.strip().split())
        if len(line) < 24:
            continue
        lower = line.lower()
        if not any(keyword in lower for keyword in keywords):
            continue
        clipped = _clip(_redact(line), 260)
        if clipped in seen:
            continue
        seen.add(clipped)
        found.append(clipped)
        if len(found) >= _MAX_DIGEST_SNIPPETS:
            break
    return found


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _redact(text: str) -> str:
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)


def _trace_url(thread_id: str, project: str) -> str | None:
    resolved = get_langsmith_trace_url(thread_id, project_name=project)
    if resolved:
        return resolved
    tenant_id = os.environ.get("LANGSMITH_TENANT_ID_PROD")
    if tenant_id and _looks_uuid(project):
        host_url = os.environ.get("LANGSMITH_URL_PROD", "https://smith.langchain.com")
        return f"{host_url}/o/{tenant_id}/projects/p/{project}/t/{thread_id}"
    return None


def _looks_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (TypeError, ValueError):
        return False
    return True
