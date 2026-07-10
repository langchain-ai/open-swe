"""Parse Azure DevOps Service Hook payloads (no REST calls)."""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlparse

from .github_comments import OPEN_SWE_TAGS

AZURE_DEVOPS_PR_COMMENT_EVENT_TYPES = frozenset(
    {
        "ms.vss-code.git-pullrequest-comment-event",
        "git.pullrequest.commented",
    }
)

SUPPORTED_EVENT_TYPES = frozenset({"workitem.commented"}).union(AZURE_DEVOPS_PR_COMMENT_EVENT_TYPES)


def _path_segments_before_apis(path: str) -> list[str]:
    segments = [s for s in path.split("/") if s]
    trimmed: list[str] = []
    for seg in segments:
        if seg == "_apis":
            break
        trimmed.append(seg)
    return trimmed


def _looks_like_uuid(segment: str) -> bool:
    try:
        uuid.UUID(segment.strip())
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def _team_project_from_resource(resource: dict[str, Any]) -> str | None:
    fields = resource.get("fields")
    if not isinstance(fields, dict):
        return None
    tp = fields.get("System.TeamProject")
    if isinstance(tp, str) and tp.strip():
        return tp.strip()
    return None


def _org_project_from_api_url(
    url: str,
    team_project: str | None = None,
) -> tuple[str | None, str | None]:
    parsed = urlparse(url.strip().rstrip("/"))
    trimmed = _path_segments_before_apis(parsed.path)
    host = (parsed.hostname or "").lower()

    if host == "dev.azure.com":
        if not trimmed:
            return None, None
        org = trimmed[0]
        if len(trimmed) == 1:
            return (org, team_project) if team_project else (None, None)
        second = trimmed[1]
        if _looks_like_uuid(second):
            return (org, team_project) if team_project else (None, None)
        return org, second

    if host.endswith(".visualstudio.com"):
        org = host[: -len(".visualstudio.com")]
        if org and len(trimmed) >= 1:
            return org, trimmed[0]
        return None, None

    return None, None


def parse_org_project_from_service_hook(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    resource = payload.get("resource")
    team_project = _team_project_from_resource(resource) if isinstance(resource, dict) else None

    containers = payload.get("resourceContainers") or {}
    if isinstance(containers, dict):
        proj_url = (containers.get("project") or {}).get("baseUrl") or ""
        if isinstance(proj_url, str) and proj_url.strip():
            result = _org_project_from_api_url(proj_url, team_project)
            if result[0] and result[1]:
                return result

    if isinstance(resource, dict):
        direct = resource.get("url")
        if isinstance(direct, str) and direct.strip():
            result = _org_project_from_api_url(direct, team_project)
            if result[0] and result[1]:
                return result
        links = resource.get("_links")
        if isinstance(links, dict):
            self_link = (links.get("self") or {}).get("href")
            if isinstance(self_link, str) and self_link.strip():
                result = _org_project_from_api_url(self_link, team_project)
                if result[0] and result[1]:
                    return result

    return None, None


def extract_work_item_id_from_payload(payload: dict[str, Any]) -> int | None:
    resource = payload.get("resource")
    if not isinstance(resource, dict):
        return None
    for key in ("id", "workItemId"):
        val = resource.get(key)
        if isinstance(val, int):
            return val
        if isinstance(val, str) and val.isdigit():
            return int(val)
    return None


def extract_work_item_title_from_payload(payload: dict[str, Any]) -> str | None:
    resource = payload.get("resource")
    if not isinstance(resource, dict):
        return None
    fields = resource.get("fields")
    if not isinstance(fields, dict):
        return None
    title = fields.get("System.Title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return None


def extract_work_item_description_from_payload(payload: dict[str, Any]) -> str:
    resource = payload.get("resource")
    if not isinstance(resource, dict):
        return ""
    fields = resource.get("fields")
    if not isinstance(fields, dict):
        return ""
    desc = fields.get("System.Description")
    return desc.strip() if isinstance(desc, str) else ""


def extract_trigger_comment_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    dm = payload.get("detailedMessage")
    if isinstance(dm, dict):
        chunks.append(str(dm.get("text") or dm.get("markdown") or dm.get("html") or ""))
    msg = payload.get("message")
    if isinstance(msg, dict):
        chunks.append(str(msg.get("text") or msg.get("html") or ""))
    resource = payload.get("resource")
    if isinstance(resource, dict):
        fields = resource.get("fields")
        if isinstance(fields, dict):
            chunks.append(str(fields.get("System.History") or ""))
    return "\n".join(chunks)


def _contains_trigger_tag(text: str) -> bool:
    lower = text.lower()
    return any(tag in lower for tag in OPEN_SWE_TAGS)


def git_ref_to_branch_short_name(ref: str) -> str:
    r = ref.strip()
    if r.startswith("refs/heads/"):
        return r[len("refs/heads/") :]
    return r


def organization_from_azure_devops_git_remote_url(remote_url: str) -> str | None:
    if not remote_url or not isinstance(remote_url, str):
        return None
    parsed = urlparse(remote_url.strip())
    host = (parsed.hostname or "").lower()
    path = parsed.path.strip("/")
    if host == "dev.azure.com":
        parts = [p for p in path.split("/") if p]
        return parts[0] if parts else None
    if host.endswith(".visualstudio.com"):
        return host[: -len(".visualstudio.com")]
    return None


def parse_org_project_from_pr_comment_payload(
    payload: dict[str, Any],
) -> tuple[str | None, str | None]:
    resource = payload.get("resource")
    if not isinstance(resource, dict):
        return None, None
    pr = resource.get("pullRequest")
    if not isinstance(pr, dict):
        return None, None
    repo = pr.get("repository")
    if not isinstance(repo, dict):
        return None, None
    project: str | None = None
    proj_obj = repo.get("project")
    if isinstance(proj_obj, dict):
        pn = proj_obj.get("name")
        if isinstance(pn, str) and pn.strip():
            project = pn.strip()
    remote = (repo.get("remoteUrl") or "").strip()
    org = organization_from_azure_devops_git_remote_url(remote)
    if org and project:
        return org, project
    return None, None


def extract_pull_request_context_from_comment_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    resource = payload.get("resource")
    if not isinstance(resource, dict):
        return out
    pr = resource.get("pullRequest")
    if not isinstance(pr, dict):
        return out
    title = pr.get("title")
    if isinstance(title, str):
        out["title"] = title
    desc = pr.get("description")
    if isinstance(desc, str):
        out["description"] = desc
    src = pr.get("sourceRefName")
    if isinstance(src, str) and src.strip():
        out["source_ref_name"] = src.strip()
        out["source_branch_short_name"] = git_ref_to_branch_short_name(src)
    tgt = pr.get("targetRefName")
    if isinstance(tgt, str) and tgt.strip():
        out["target_ref_name"] = tgt.strip()
    links = pr.get("_links")
    if isinstance(links, dict):
        web = (links.get("web") or {}).get("href")
        if isinstance(web, str) and web.strip():
            out["pr_web_url"] = web.strip()
    repo = pr.get("repository")
    if isinstance(repo, dict):
        rn = repo.get("name")
        if isinstance(rn, str) and rn.strip():
            out["repository_name"] = rn.strip()
    comment = resource.get("comment")
    if isinstance(comment, dict):
        content = comment.get("content")
        if isinstance(content, str):
            out["trigger_comment_text"] = content
    return out


def azure_devops_pr_comment_payload_triggers_agent(payload: dict[str, Any]) -> bool:
    resource = payload.get("resource")
    if not isinstance(resource, dict):
        return False
    comment = resource.get("comment")
    if not isinstance(comment, dict):
        return False
    body = (comment.get("content") or "").strip()
    return bool(body) and _contains_trigger_tag(body)


def azure_devops_service_hook_should_process(payload: dict[str, Any]) -> bool:
    event_type = (payload.get("eventType") or "").strip()
    if event_type not in SUPPORTED_EVENT_TYPES:
        return False
    if event_type in AZURE_DEVOPS_PR_COMMENT_EVENT_TYPES:
        return azure_devops_pr_comment_payload_triggers_agent(payload)
    return _contains_trigger_tag(extract_trigger_comment_text(payload))
