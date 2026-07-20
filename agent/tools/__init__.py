import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

_TOOL_MODULES = {
    "add_finding": ".add_finding",
    "approve_plan": ".approve_plan",
    "enter_plan_mode": ".enter_plan_mode",
    "fetch_review_diff": ".fetch_review_diff",
    "fetch_url": ".fetch_url",
    "http_request": ".http_request",
    "linear_comment": ".linear_comment",
    "linear_create_issue": ".linear_create_issue",
    "linear_delete_issue": ".linear_delete_issue",
    "linear_get_issue": ".linear_get_issue",
    "linear_get_issue_comments": ".linear_get_issue_comments",
    "linear_list_teams": ".linear_list_teams",
    "linear_search_issues": ".linear_search_issues",
    "linear_update_issue": ".linear_update_issue",
    "list_findings": ".list_findings",
    "list_review_findings": ".list_review_findings",
    "open_pull_request": ".open_pull_request",
    "publish_review": ".publish_review",
    "read_repo_file": ".read_repo_file",
    "report_platform_issue": ".report_platform_issue",
    "request_pr_review": ".request_pr_review",
    "reply_to_finding_thread": ".reply_to_finding_thread",
    "resolve_finding_thread": ".resolve_finding_thread",
    "save_plan": ".save_plan",
    "schedule_thread_wakeup": ".schedule_thread_wakeup",
    "search_repo_code": ".search_repo_code",
    "slack_add_reaction": ".slack_add_reaction",
    "slack_read_thread_messages": ".slack_read_thread_messages",
    "slack_start_new_thread": ".slack_start_new_thread",
    "slack_thread_reply": ".slack_thread_reply",
    "update_finding": ".update_finding",
    "web_search": ".web_search",
}

__all__ = [
    "add_finding",
    "approve_plan",
    "enter_plan_mode",
    "fetch_review_diff",
    "fetch_url",
    "http_request",
    "linear_comment",
    "linear_create_issue",
    "linear_delete_issue",
    "linear_get_issue",
    "linear_get_issue_comments",
    "linear_list_teams",
    "linear_search_issues",
    "linear_update_issue",
    "list_findings",
    "list_review_findings",
    "open_pull_request",
    "publish_review",
    "read_repo_file",
    "report_platform_issue",
    "request_pr_review",
    "reply_to_finding_thread",
    "resolve_finding_thread",
    "save_plan",
    "schedule_thread_wakeup",
    "search_repo_code",
    "slack_add_reaction",
    "slack_read_thread_messages",
    "slack_start_new_thread",
    "slack_thread_reply",
    "update_finding",
    "web_search",
]

if TYPE_CHECKING:
    from .add_finding import add_finding
    from .approve_plan import approve_plan
    from .enter_plan_mode import enter_plan_mode
    from .fetch_review_diff import fetch_review_diff
    from .fetch_url import fetch_url
    from .http_request import http_request
    from .linear_comment import linear_comment
    from .linear_create_issue import linear_create_issue
    from .linear_delete_issue import linear_delete_issue
    from .linear_get_issue import linear_get_issue
    from .linear_get_issue_comments import linear_get_issue_comments
    from .linear_list_teams import linear_list_teams
    from .linear_search_issues import linear_search_issues
    from .linear_update_issue import linear_update_issue
    from .list_findings import list_findings
    from .list_review_findings import list_review_findings
    from .open_pull_request import open_pull_request
    from .publish_review import publish_review
    from .read_repo_file import read_repo_file
    from .reply_to_finding_thread import reply_to_finding_thread
    from .report_platform_issue import report_platform_issue
    from .request_pr_review import request_pr_review
    from .resolve_finding_thread import resolve_finding_thread
    from .save_plan import save_plan
    from .schedule_thread_wakeup import schedule_thread_wakeup
    from .search_repo_code import search_repo_code
    from .slack_add_reaction import slack_add_reaction
    from .slack_read_thread_messages import slack_read_thread_messages
    from .slack_start_new_thread import slack_start_new_thread
    from .slack_thread_reply import slack_thread_reply
    from .update_finding import update_finding
    from .web_search import web_search


def _load_export(name: str) -> Any:
    module_name = _TOOL_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


class _LazyToolsModule(ModuleType):
    def __getattribute__(self, name: str) -> Any:
        module_map = ModuleType.__getattribute__(self, "__dict__").get("_TOOL_MODULES", {})
        if name not in module_map:
            return ModuleType.__getattribute__(self, name)
        # Prefer public exports over same-named submodule attributes set by importlib.
        existing = ModuleType.__getattribute__(self, "__dict__").get(name)
        if existing is not None and not isinstance(existing, ModuleType):
            return existing
        return _load_export(name)


def __getattr__(name: str) -> Any:
    return _load_export(name)


sys.modules[__name__].__class__ = _LazyToolsModule
