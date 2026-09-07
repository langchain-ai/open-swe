"""Read-only evidence tools bounded by the installed credentials. Credentials never enter the model context."""

import base64
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx
from langchain_core.tools import BaseTool, tool

from agent.dashboard.team_credentials import (
    SUPPORTED_DD_SITES,
    DatadogCredentials,
    get_datadog_credentials,
)
from agent.github.app import (
    get_github_app_installation_id_for_repo,
    get_github_app_installation_token,
)
from agent.investigations.models import Evidence, InvestigationPolicy
from agent.store import now_iso

_SECRET = re.compile(
    r"(?i)(?:\b(?:sk-|gh[pousr]_|github_pat_|xox[baprs]-|lsv2_pt_)[\w-]+"
    r"|\bAKIA[A-Z0-9]{16}\b"
    r"|\b(?:bearer\s+)[\w.\-/+=]+"
    r"|\b(?:password|secret|api[_-]?key|access[_-]?token|authorization)\s*[:=]\s*[^\s,;]+"
    r"|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"
    r"|-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----)"
)
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_SERVICE = re.compile(r"[A-Za-z0-9_.-]{1,150}")
_METRIC = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,199}")
_SECRET_FILE = re.compile(
    r"(?i)(?:^|/)(?:\.env(?:\.[^/]*)?|\.npmrc|\.pypirc|\.netrc|\.git(?:/.*)?"
    r"|credentials\.json|token(?:\.json)?|[^/]*\.(?:pem|key|crt|p12|pfx|jks|keystore)"
    r"|[^/]*_(?:rsa|ed25519))$"
)
_MAX_RESPONSE_BYTES = 1_000_000


def redact(value: str, limit: int = 4000) -> str:
    """Remove common secret/identity forms before text reaches prompts or reports."""
    return _SECRET.sub("[redacted]", value)[:limit]


def source_url(value: str | None) -> str:
    """Keep only HTTPS source locations, without credentials or arbitrary queries."""
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return ""
        return urlunsplit(("https", parsed.netloc, parsed.path, "", ""))[:2000]
    except ValueError:
        return ""


class EvidenceCollector:
    def __init__(
        self,
        policy: InvestigationPolicy,
        *,
        window_start: datetime,
        window_end: datetime,
        before_tool_call: Callable[[], Awaitable[None]] | None = None,
        datadog_enabled: bool = False,
    ) -> None:
        self.policy = policy
        self.window_start = window_start
        self.window_end = window_end
        self.before_tool_call = before_tool_call
        self.datadog_enabled = datadog_enabled
        self.evidence: list[Evidence] = []
        self.gaps: list[str] = []
        self.checked: list[str] = []
        self._calls = 0
        self._remaining_chars = 30_000
        self.control_error: Exception | None = None

    async def _run(
        self, operation: str, call: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        # The callback must propagate control revocation, never turn it into evidence.
        if self.control_error is not None:
            raise self.control_error
        if self.before_tool_call is not None:
            try:
                await self.before_tool_call()
            except Exception as exc:
                self.control_error = exc
                raise
        self._calls += 1
        try:
            if not self.policy.enabled:
                raise PermissionError("Investigation is disabled by the workspace policy")
            if self._calls > min(40, self.policy.max_model_calls * 2):
                raise ValueError("Evidence tool-call budget exhausted")
            if self._remaining_chars <= 0:
                raise ValueError("Evidence context budget exhausted")
            result = await call()
            self.checked.append(operation)
            return result
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            gap = f"{operation}: source returned HTTP {status}."
            if status == 429:
                gap += " Rate limited; no immediate retry was attempted."
        except (ValueError, PermissionError) as exc:
            gap = f"{operation}: {redact(str(exc), 250)}."
        except Exception:  # noqa: BLE001
            gap = f"{operation}: source unavailable or response could not be read."
        self.gaps.append(gap)
        return {"gap": gap}

    def _record(
        self, *, source: str, url: str, summary: str, query: str, content: Any
    ) -> dict[str, Any]:
        observation = json.dumps(content, ensure_ascii=False)
        key = hashlib.sha256(f"{source}:{url}:{query}:{observation}".encode()).hexdigest()[:16]
        evidence = Evidence(
            id=f"{source}:{key}",
            source=source,
            url=url,
            summary=redact(summary, 1500),
            query=redact(query, 2000),
            retrieved_at=now_iso(),
        )
        if all(item.id != evidence.id for item in self.evidence):
            self.evidence.append(evidence)
        serialized = redact(observation, min(4000, self._remaining_chars))
        if len(observation) > len(serialized):
            gap = "Evidence content was bounded or redacted; source links retain the full context."
            if gap not in self.gaps:
                self.gaps.append(gap)
        self._remaining_chars -= len(serialized)
        return {"evidence_id": evidence.id, "source_url": url, "observation": serialized}

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            async with client.stream(method, url, **kwargs) as response:
                response.raise_for_status()
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > _MAX_RESPONSE_BYTES:
                        raise ValueError("Source response exceeded the size budget")
                return json.loads(chunks)

    async def _github(self, repository: str, suffix: str, *, params=None) -> Any:
        if not _REPOSITORY.fullmatch(repository):
            raise PermissionError("Repository must be named as owner/repository")
        owner, name = repository.split("/")
        installation = await get_github_app_installation_id_for_repo(owner, name)
        if installation is None:
            raise PermissionError("GitHub App repository installation is unavailable")
        token = await get_github_app_installation_token(
            installation_id=installation, repositories=[name], permissions={"contents": "read"}
        )
        if not token:
            raise PermissionError("GitHub App read access is unavailable")
        url = f"https://api.github.com/repos/{repository}/{suffix}"
        if suffix == "search/code":
            url = "https://api.github.com/search/code"
        return await self._request(
            "GET",
            url,
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def _datadog(self, service: str) -> DatadogCredentials:
        if not _SERVICE.fullmatch(service):
            raise PermissionError("Service must be a literal service name")
        credentials = await get_datadog_credentials()
        if credentials is None or credentials.site not in SUPPORTED_DD_SITES:
            raise PermissionError("Datadog connection is unavailable")
        return credentials

    async def _aggregate(self, service: str, kind: str, status: str) -> dict[str, Any]:
        credentials = await self._datadog(service)
        query = f"service:{service}"
        if status != "all":
            query += f" {'status:' if kind == 'logs' else '@error:'}{status}"
        payload = {
            "filter": {
                "query": query,
                "from": self.window_start.isoformat(),
                "to": self.window_end.isoformat(),
            },
            "compute": [{"aggregation": "count", "type": "timeseries", "interval": "5m"}],
        }
        result = await self._request(
            "POST",
            f"https://api.{credentials.site}/api/v2/{kind}/analytics/aggregate",
            headers={"DD-API-KEY": credentials.api_key, "DD-APPLICATION-KEY": credentials.app_key},
            json=payload,
        )
        # Aggregate responses contain only numeric series; ignore unsolicited attributes.
        aggregate_data = result.get("data")
        if isinstance(aggregate_data, dict) and isinstance(aggregate_data.get("buckets"), list):
            buckets = aggregate_data["buckets"]
        elif kind == "spans" and isinstance(aggregate_data, list):
            buckets = [{"computes": row["attributes"].get("compute")} for row in aggregate_data]
        else:
            raise ValueError("Datadog returned an invalid aggregate response")
        if any(not isinstance(bucket.get("computes"), dict) for bucket in buckets):
            raise ValueError("Datadog aggregates did not contain numeric observations")
        counts = [bucket.get("computes", {}) for bucket in buckets[:100]]
        data = _numeric_aggregates(counts)
        query_context = json.dumps(payload["filter"])
        url = (
            f"https://app.{credentials.site}/{'logs' if kind == 'logs' else 'apm/traces'}?"
            + urlencode(
                {
                    "query": query,
                    "from_ts": int(self.window_start.timestamp() * 1000),
                    "to_ts": int(self.window_end.timestamp() * 1000),
                }
            )
        )
        return self._record(
            source="datadog",
            url=url,
            query=query_context,
            summary=f"{kind} count aggregates for {service}: {json.dumps(data)[:900]}",
            content=data,
        )

    def tools(self) -> list[BaseTool]:
        @tool
        async def investigate_read_repo_file(repository: str, path: str, ref: str = "HEAD") -> dict:
            """Read one non-secret source file in an allowed owner/repo at a revision."""

            async def read():
                parts = PurePosixPath(path).parts
                if not path or path.startswith("/") or ".." in parts or _SECRET_FILE.search(path):
                    raise PermissionError("File path is outside the permitted source-file scope")
                if len(path) > 500 or len(ref) > 200:
                    raise ValueError("File path or revision is too long")
                data = await self._github(
                    repository, f"contents/{quote(path, safe='/')}", params={"ref": ref}
                )
                if not isinstance(data, dict) or data.get("type") != "file":
                    raise ValueError("The source is not a file")
                if data.get("encoding") != "base64" or data.get("size", 0) > 100_000:
                    raise ValueError("File exceeds the permitted text-file size")
                content = base64.b64decode(data["content"]).decode("utf-8")
                url = f"https://github.com/{repository}/blob/{quote(ref, safe='')}/{quote(path, safe='/')}"
                return self._record(
                    source="github",
                    url=url,
                    query=f"{repository}:{path}@{ref}",
                    summary=f"Source file {repository}/{path} at {ref}; deployed revision is not verified.",
                    content=content,
                )

            return await self._run("GitHub source file", read)

        @tool
        async def investigate_search_repo_code(repository: str, term: str) -> dict:
            """Search an allowed owner/repo for a plain symbol or phrase (no query operators)."""

            async def search():
                if not re.fullmatch(r"[A-Za-z0-9_ ./-]{1,120}", term):
                    raise ValueError("Search requires a plain symbol or phrase")
                query = f'"{term}" repo:{repository}'
                data = await self._github(
                    repository, "search/code", params={"q": query, "per_page": 20}
                )
                matches = [
                    {"path": item.get("path"), "sha": item.get("sha")}
                    for item in data.get("items", [])[:20]
                    if not _SECRET_FILE.search(item.get("path", ""))
                ]
                if data.get("incomplete_results") or data.get("total_count", 0) > len(matches):
                    self.gaps.append("GitHub code search returned a partial result set.")
                return self._record(
                    source="github",
                    url=f"https://github.com/{repository}/search?"
                    + urlencode({"q": term, "type": "code"}),
                    summary=f"Code search in {repository} found {len(matches)} visible matches.",
                    query=query,
                    content=matches,
                )

            return await self._run("GitHub code search", search)

        @tool
        async def investigate_recent_commits(repository: str) -> dict:
            """Read at most 20 commits in the fixed investigation time window."""

            async def commits():
                params = {
                    "since": self.window_start.isoformat(),
                    "until": self.window_end.isoformat(),
                    "per_page": 20,
                }
                data = await self._github(repository, "commits", params=params)
                commits = [
                    {
                        "sha": row["sha"],
                        "message": redact(row["commit"]["message"], 300),
                        "date": row["commit"]["committer"]["date"],
                    }
                    for row in data[:20]
                ]
                if len(data) >= 20:
                    self.gaps.append("Recent commits are limited to the first 20 results.")
                return self._record(
                    source="github",
                    url=f"https://github.com/{repository}/commits",
                    query=json.dumps(params),
                    summary=f"Found {len(commits)} recent commits in {repository}; deployment is unverified.",
                    content=commits,
                )

            return await self._run("GitHub recent commits", commits)

        @tool
        async def investigate_datadog_logs(
            service: str, status: Literal["error", "warn", "info", "all"] = "error"
        ) -> dict:
            """Read log count aggregates for an allowed service in the fixed time window."""
            return await self._run(
                "Datadog log aggregates", lambda: self._aggregate(service, "logs", status)
            )

        @tool
        async def investigate_datadog_spans(service: str, errors_only: bool = True) -> dict:
            """Read indexed span counts for an allowed service in the fixed time window."""
            return await self._run(
                "Datadog indexed span aggregates",
                lambda: self._aggregate(service, "spans", "1" if errors_only else "all"),
            )

        @tool
        async def investigate_datadog_metric(service: str, metric: str) -> dict:
            """Read an average metric series scoped to one allowed service and the fixed window."""

            async def read_metric():
                credentials = await self._datadog(service)
                if not _METRIC.fullmatch(metric):
                    raise ValueError("Metric must be a plain metric name")
                query = f"avg:{metric}{{service:{service}}}.rollup(avg,300)"
                params = {
                    "query": query,
                    "from": int(self.window_start.timestamp()),
                    "to": int(self.window_end.timestamp()),
                }
                data = await self._request(
                    "GET",
                    f"https://api.{credentials.site}/api/v1/query",
                    params=params,
                    headers={
                        "DD-API-KEY": credentials.api_key,
                        "DD-APPLICATION-KEY": credentials.app_key,
                    },
                )
                if data.get("status") == "error":
                    raise ValueError("Datadog rejected the metric query")
                series = [row.get("pointlist", [])[:100] for row in data.get("series", [])[:20]]
                return self._record(
                    source="datadog",
                    url=f"https://app.{credentials.site}/metric/explorer?"
                    + urlencode({"query": query}),
                    query=json.dumps(params),
                    summary=f"Metric {metric} for {service}: {json.dumps(series)[:900]}",
                    content=series,
                )

            return await self._run("Datadog metric query", read_metric)

        result: list[BaseTool] = [
            investigate_read_repo_file,
            investigate_search_repo_code,
            investigate_recent_commits,
        ]
        if self.datadog_enabled:
            result.extend(
                [investigate_datadog_logs, investigate_datadog_spans, investigate_datadog_metric]
            )
        return result


def _numeric_aggregates(value: Any) -> Any:
    """Accept numeric values/timestamps, dropping provider-supplied free text."""
    if isinstance(value, (int, float)) or value is None:
        return value
    if isinstance(value, list):
        return [_numeric_aggregates(item) for item in value[:100]]
    if isinstance(value, dict):
        return {
            key: _numeric_aggregates(item)
            for key, item in value.items()
            if re.fullmatch(r"c\d+|value|time", key)
        }
    if isinstance(value, str) and re.fullmatch(r"[0-9TZ:+.\-]+", value):
        return value
    return None
