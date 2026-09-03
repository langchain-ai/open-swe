import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

_TOOL_MODULES = {
    "add_finding": ".add_finding",
    "approve_plan": ".approve_plan",
    "background_execute": ".background_execute",
    "background_task": ".background_execute",
    "capture_environment_snapshot": ".environments",
    "create_automation": ".automations",
    "create_sandbox_file_download_url": ".create_sandbox_file_download_url",
    "create_sandbox_service_url": ".create_sandbox_service_url",
    "delete_automation": ".automations",
    "delete_environment": ".environments",
    "enter_plan_mode": ".enter_plan_mode",
    "fetch_review_diff": ".fetch_review_diff",
    "fetch_self_review_diff": ".inline_review",
    "fetch_url": ".fetch_url",
    "get_thread": ".threads",
    "http_request": ".http_request",
    "linear_comment": "agent.linear.tools.comment",
    "linear_create_issue": "agent.linear.tools.create_issue",
    "linear_delete_issue": "agent.linear.tools.delete_issue",
    "linear_get_issue": "agent.linear.tools.get_issue",
    "linear_get_issue_comments": "agent.linear.tools.get_issue_comments",
    "linear_list_teams": "agent.linear.tools.list_teams",
    "linear_search_issues": "agent.linear.tools.search_issues",
    "linear_update_issue": "agent.linear.tools.update_issue",
    "list_automations": ".automations",
    "list_environments": ".environments",
    "list_findings": ".list_findings",
    "list_inline_findings": ".inline_review",
    "list_review_findings": ".list_review_findings",
    "list_threads": ".threads",
    "manage_baby_sit": ".manage_baby_sit",
    "manage_code_channel": "agent.slack.tools.manage_code_channel",
    "manage_thread": ".threads",
    "notify_automation_channel": ".notify_automation_channel",
    "open_pull_request": ".open_pull_request",
    "output_iframe": ".output_iframe",
    "publish_review": ".publish_review",
    "read_repo_file": "agent.github.tools.read_repo_file",
    "record_inline_finding": ".inline_review",
    "read_user_settings": ".read_user_settings",
    "recreate_sandbox": ".recreate_sandbox",
    "report_platform_issue": ".report_platform_issue",
    "request_pr_review": "agent.slack.tools.request_pr_review",
    "reply_to_finding_thread": ".reply_to_finding_thread",
    "resolve_finding_thread": ".resolve_finding_thread",
    "delete_organization_skill": ".organization_skills",
    "save_environment": ".environments",
    "save_organization_skill": ".organization_skills",
    "save_plan": ".save_plan",
    "sandbox_reset": ".sandbox_reset",
    "save_user_instructions": ".save_user_instructions",
    "save_user_skill": ".user_skills",
    "delete_user_skill": ".user_skills",
    "schedule_thread_wakeup": ".schedule_thread_wakeup",
    "search_repo_code": "agent.github.tools.search_repo_code",
    "set_inline_finding_disposition": ".inline_review",
    "slack_add_reaction": "agent.slack.tools.add_reaction",
    "slack_attach_html": "agent.slack.tools.attach_html",
    "slack_move_thread": "agent.slack.tools.move_thread",
    "slack_read_thread_messages": "agent.slack.tools.read_thread_messages",
    "slack_start_new_thread": "agent.slack.tools.start_new_thread",
    "slack_thread_reply": "agent.slack.tools.thread_reply",
    "trigger_automation": ".automations",
    "update_automation": ".automations",
    "update_finding": ".update_finding",
    "web_search": ".web_search",
}

__all__ = [
    "add_finding",
    "approve_plan",
    "background_execute",
    "background_task",
    "capture_environment_snapshot",
    "create_automation",
    "create_sandbox_file_download_url",
    "create_sandbox_service_url",
    "delete_automation",
    "delete_environment",
    "enter_plan_mode",
    "fetch_review_diff",
    "fetch_self_review_diff",
    "fetch_url",
    "get_thread",
    "http_request",
    "linear_comment",
    "linear_create_issue",
    "linear_delete_issue",
    "linear_get_issue",
    "linear_get_issue_comments",
    "linear_list_teams",
    "linear_search_issues",
    "linear_update_issue",
    "list_automations",
    "list_environments",
    "list_findings",
    "list_inline_findings",
    "list_review_findings",
    "list_threads",
    "manage_baby_sit",
    "manage_code_channel",
    "manage_thread",
    "notify_automation_channel",
    "open_pull_request",
    "output_iframe",
    "publish_review",
    "read_repo_file",
    "record_inline_finding",
    "read_user_settings",
    "recreate_sandbox",
    "report_platform_issue",
    "request_pr_review",
    "reply_to_finding_thread",
    "resolve_finding_thread",
    "save_environment",
    "save_organization_skill",
    "delete_organization_skill",
    "save_plan",
    "sandbox_reset",
    "save_user_instructions",
    "save_user_skill",
    "delete_user_skill",
    "schedule_thread_wakeup",
    "search_repo_code",
    "set_inline_finding_disposition",
    "slack_add_reaction",
    "slack_attach_html",
    "slack_move_thread",
    "slack_read_thread_messages",
    "slack_start_new_thread",
    "slack_thread_reply",
    "trigger_automation",
    "update_automation",
    "update_finding",
    "web_search",
]

if TYPE_CHECKING:
    from agent.github.tools.read_repo_file import read_repo_file
    from agent.github.tools.search_repo_code import search_repo_code
    from agent.linear.tools.comment import linear_comment
    from agent.linear.tools.create_issue import linear_create_issue
    from agent.linear.tools.delete_issue import linear_delete_issue
    from agent.linear.tools.get_issue import linear_get_issue
    from agent.linear.tools.get_issue_comments import linear_get_issue_comments
    from agent.linear.tools.list_teams import linear_list_teams
    from agent.linear.tools.search_issues import linear_search_issues
    from agent.linear.tools.update_issue import linear_update_issue
    from agent.slack.tools.add_reaction import slack_add_reaction
    from agent.slack.tools.attach_html import slack_attach_html
    from agent.slack.tools.manage_code_channel import manage_code_channel
    from agent.slack.tools.move_thread import slack_move_thread
    from agent.slack.tools.read_thread_messages import slack_read_thread_messages
    from agent.slack.tools.request_pr_review import request_pr_review
    from agent.slack.tools.start_new_thread import slack_start_new_thread
    from agent.slack.tools.thread_reply import slack_thread_reply
    from agent.tools.add_finding import add_finding
    from agent.tools.approve_plan import approve_plan
    from agent.tools.automations import (
        create_automation,
        delete_automation,
        list_automations,
        trigger_automation,
        update_automation,
    )
    from agent.tools.background_execute import background_execute, background_task
    from agent.tools.create_sandbox_file_download_url import create_sandbox_file_download_url
    from agent.tools.create_sandbox_service_url import create_sandbox_service_url
    from agent.tools.enter_plan_mode import enter_plan_mode
    from agent.tools.environments import (
        capture_environment_snapshot,
        delete_environment,
        list_environments,
        save_environment,
    )
    from agent.tools.fetch_review_diff import fetch_review_diff
    from agent.tools.fetch_url import fetch_url
    from agent.tools.http_request import http_request
    from agent.tools.inline_review import (
        fetch_self_review_diff,
        list_inline_findings,
        record_inline_finding,
        set_inline_finding_disposition,
    )
    from agent.tools.list_findings import list_findings
    from agent.tools.list_review_findings import list_review_findings
    from agent.tools.manage_baby_sit import manage_baby_sit
    from agent.tools.notify_automation_channel import notify_automation_channel
    from agent.tools.open_pull_request import open_pull_request
    from agent.tools.organization_skills import delete_organization_skill, save_organization_skill
    from agent.tools.output_iframe import output_iframe
    from agent.tools.publish_review import publish_review
    from agent.tools.read_user_settings import read_user_settings
    from agent.tools.recreate_sandbox import recreate_sandbox
    from agent.tools.reply_to_finding_thread import reply_to_finding_thread
    from agent.tools.report_platform_issue import report_platform_issue
    from agent.tools.resolve_finding_thread import resolve_finding_thread
    from agent.tools.sandbox_reset import sandbox_reset
    from agent.tools.save_plan import save_plan
    from agent.tools.save_user_instructions import save_user_instructions
    from agent.tools.schedule_thread_wakeup import schedule_thread_wakeup
    from agent.tools.threads import get_thread, list_threads, manage_thread
    from agent.tools.update_finding import update_finding
    from agent.tools.user_skills import delete_user_skill, save_user_skill
    from agent.tools.web_search import web_search


def _load_export(name: str) -> Any:
    module_name = _TOOL_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    package = None if module_name.startswith("agent.") else __name__
    value = getattr(import_module(module_name, package), name)
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
