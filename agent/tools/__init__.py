import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

_TOOL_MODULES = {
    "add_finding": ".add_finding",
    "capture_environment_snapshot": ".environments",
    "create_automation": ".automations",
    "delete_automation": ".automations",
    "delete_environment": ".environments",
    "fetch_review_diff": ".fetch_review_diff",
    "get_thread": ".threads",
    "linear_comment": "agent.linear.tools.comment",
    "list_automations": ".automations",
    "list_environments": ".environments",
    "list_findings": ".list_findings",
    "list_review_findings": ".list_review_findings",
    "list_threads": ".threads",
    "manage_baby_sit": ".manage_baby_sit",
    "manage_code_channel": "agent.slack.tools.manage_code_channel",
    "manage_thread": ".threads",
    "mark_question_answered": ".mark_question_answered",
    "notify_automation_channel": ".notify_automation_channel",
    "open_pull_request": ".open_pull_request",
    "publish_review": ".publish_review",
    "read_repo_file": "agent.github.tools.read_repo_file",
    "read_user_settings": ".read_user_settings",
    "recreate_sandbox": ".recreate_sandbox",
    "report_platform_issue": ".report_platform_issue",
    "request_pr_review": "agent.slack.tools.request_pr_review",
    "reply_to_finding_thread": ".reply_to_finding_thread",
    "resolve_finding_thread": ".resolve_finding_thread",
    "delete_organization_skill": ".organization_skills",
    "save_environment": ".environments",
    "save_organization_skill": ".organization_skills",
    "sandbox_reset": ".sandbox_reset",
    "save_user_instructions": ".save_user_instructions",
    "save_user_skill": ".user_skills",
    "delete_user_skill": ".user_skills",
    "schedule_thread_wakeup": ".schedule_thread_wakeup",
    "search_repo_code": "agent.github.tools.search_repo_code",
    "slack_add_reaction": "agent.slack.tools.add_reaction",
    "slack_attach_html": "agent.slack.tools.attach_html",
    "slack_move_thread": "agent.slack.tools.move_thread",
    "slack_read_thread_messages": "agent.slack.tools.read_thread_messages",
    "slack_start_new_thread": "agent.slack.tools.start_new_thread",
    "slack_thread_reply": "agent.slack.tools.thread_reply",
    "trigger_automation": ".automations",
    "update_automation": ".automations",
    "update_finding": ".update_finding",
}

__all__ = [
    "add_finding",
    "capture_environment_snapshot",
    "create_automation",
    "delete_automation",
    "delete_environment",
    "fetch_review_diff",
    "get_thread",
    "linear_comment",
    "list_automations",
    "list_environments",
    "list_findings",
    "list_review_findings",
    "list_threads",
    "manage_baby_sit",
    "manage_code_channel",
    "manage_thread",
    "mark_question_answered",
    "notify_automation_channel",
    "open_pull_request",
    "publish_review",
    "read_repo_file",
    "read_user_settings",
    "recreate_sandbox",
    "report_platform_issue",
    "request_pr_review",
    "reply_to_finding_thread",
    "resolve_finding_thread",
    "save_environment",
    "save_organization_skill",
    "delete_organization_skill",
    "sandbox_reset",
    "save_user_instructions",
    "save_user_skill",
    "delete_user_skill",
    "schedule_thread_wakeup",
    "search_repo_code",
    "slack_add_reaction",
    "slack_attach_html",
    "slack_move_thread",
    "slack_read_thread_messages",
    "slack_start_new_thread",
    "slack_thread_reply",
    "trigger_automation",
    "update_automation",
    "update_finding",
]

if TYPE_CHECKING:
    from agent.github.tools.read_repo_file import read_repo_file
    from agent.github.tools.search_repo_code import search_repo_code
    from agent.linear.tools.comment import linear_comment
    from agent.slack.tools.add_reaction import slack_add_reaction
    from agent.slack.tools.attach_html import slack_attach_html
    from agent.slack.tools.manage_code_channel import manage_code_channel
    from agent.slack.tools.move_thread import slack_move_thread
    from agent.slack.tools.read_thread_messages import slack_read_thread_messages
    from agent.slack.tools.request_pr_review import request_pr_review
    from agent.slack.tools.start_new_thread import slack_start_new_thread
    from agent.slack.tools.thread_reply import slack_thread_reply
    from agent.tools.add_finding import add_finding
    from agent.tools.automations import (
        create_automation,
        delete_automation,
        list_automations,
        trigger_automation,
        update_automation,
    )
    from agent.tools.environments import (
        capture_environment_snapshot,
        delete_environment,
        list_environments,
        save_environment,
    )
    from agent.tools.fetch_review_diff import fetch_review_diff
    from agent.tools.list_findings import list_findings
    from agent.tools.list_review_findings import list_review_findings
    from agent.tools.manage_baby_sit import manage_baby_sit
    from agent.tools.mark_question_answered import mark_question_answered
    from agent.tools.notify_automation_channel import notify_automation_channel
    from agent.tools.open_pull_request import open_pull_request
    from agent.tools.organization_skills import delete_organization_skill, save_organization_skill
    from agent.tools.publish_review import publish_review
    from agent.tools.read_user_settings import read_user_settings
    from agent.tools.recreate_sandbox import recreate_sandbox
    from agent.tools.reply_to_finding_thread import reply_to_finding_thread
    from agent.tools.report_platform_issue import report_platform_issue
    from agent.tools.resolve_finding_thread import resolve_finding_thread
    from agent.tools.sandbox_reset import sandbox_reset
    from agent.tools.save_user_instructions import save_user_instructions
    from agent.tools.schedule_thread_wakeup import schedule_thread_wakeup
    from agent.tools.threads import get_thread, list_threads, manage_thread
    from agent.tools.update_finding import update_finding
    from agent.tools.user_skills import delete_user_skill, save_user_skill


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
