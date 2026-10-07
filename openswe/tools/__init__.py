import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

_TOOL_MODULES = {
    "add_finding": ".add_finding",
    "assign_human_reviewer": ".request_human_review",
    "auto_assign_human_reviewer": ".request_human_review",
    "background_execute": ".background_execute",
    "background_task": ".background_task",
    "create_automation": ".automations",
    "create_sandbox_file_download_url": ".create_sandbox_file_download_url",
    "delete_automation": ".automations",
    "delete_workspace": ".workspaces",
    "dismiss_human_review_request": ".request_human_review",
    "expedite_pr_approval": ".expedite_pr_approval",
    "expose_port": ".expose_port",
    "fetch_review_diff": ".fetch_review_diff",
    "fetch_url": ".fetch_url",
    "get_thread": ".threads",
    "http_request": ".http_request",
    "list_automations": ".automations",
    "list_event_types": ".listen_events",
    "listen_events": ".listen_events",
    "list_workspaces": ".workspaces",
    "list_findings": ".list_findings",
    "list_review_findings": ".list_review_findings",
    "link_pull_request": ".open_pull_request",
    "list_threads": ".threads",
    "manage_baby_sit": ".manage_baby_sit",
    "switch_to_performance_model": ".switch_to_performance_model",
    "manage_code_channel": "openswe.slack.tools.manage_code_channel",
    "manage_incident": "openswe.incidents.tools",
    "manage_thread": ".threads",
    "merge_expedited_pr": ".merge_expedited_pr",
    "open_pull_request": ".open_pull_request",
    "output_iframe": ".output_iframe",
    "publish_review": ".publish_review",
    "read_only_sql": ".read_only_sql",
    "read_store_item": ".read_store_item",
    "read_repo_file": "openswe.github.tools.read_repo_file",
    "read_user_settings": ".read_user_settings",
    "record_human_input": ".record_human_input",
    "recreate_sandbox": ".recreate_sandbox",
    "refresh_workspace_start": ".workspaces",
    "configure_repository": ".workspaces",
    "report_platform_issue": ".report_platform_issue",
    "request_human_review": ".request_human_review",
    "request_pr_review": "openswe.slack.tools.request_pr_review",
    "request_service_connection": ".request_service_connection",
    "reply_to_finding_thread": ".reply_to_finding_thread",
    "resolve_finding_thread": ".resolve_finding_thread",
    "delete_organization_skill": ".organization_skills",
    "publish_workspace": ".workspaces",
    "save_organization_skill": ".organization_skills",
    "save_plan": ".save_plan",
    "save_user_instructions": ".save_user_instructions",
    "save_user_settings": ".save_user_settings",
    "save_user_skill": ".user_skills",
    "delete_user_skill": ".user_skills",
    "schedule_thread_wakeup": ".schedule_thread_wakeup",
    "search_pull_requests": ".search_pull_requests",
    "search_repo_code": "openswe.github.tools.search_repo_code",
    "start_thread": ".threads",
    "slack_add_reaction": "openswe.slack.tools.add_reaction",
    "slack_attach_html": "openswe.slack.tools.attach_html",
    "slack_list_channel_members": "openswe.slack.tools.channels",
    "slack_list_channels": "openswe.slack.tools.channels",
    "slack_move_thread": "openswe.slack.tools.move_thread",
    "slack_no_reply_needed": "openswe.slack.tools.no_reply_needed",
    "slack_post_message": "openswe.slack.tools.channels",
    "slack_read_channel_messages": "openswe.slack.tools.read_channel_messages",
    "slack_read_thread_messages": "openswe.slack.tools.read_thread_messages",
    "slack_reply": "openswe.slack.tools.reply",
    "slack_breakout_thread": "openswe.slack.tools.start_new_thread",
    "submit_thread_feedback": ".submit_thread_feedback",
    "suggest_task": ".suggest_task",
    "trigger_automation": ".automations",
    "update_automation": ".automations",
    "update_finding": ".update_finding",
    "web_search": ".web_search",
}

__all__ = [
    "add_finding",
    "assign_human_reviewer",
    "auto_assign_human_reviewer",
    "background_execute",
    "background_task",
    "create_automation",
    "create_sandbox_file_download_url",
    "delete_automation",
    "delete_workspace",
    "dismiss_human_review_request",
    "expedite_pr_approval",
    "expose_port",
    "fetch_review_diff",
    "fetch_url",
    "get_thread",
    "http_request",
    "list_automations",
    "list_event_types",
    "listen_events",
    "list_workspaces",
    "list_findings",
    "list_review_findings",
    "link_pull_request",
    "list_threads",
    "manage_baby_sit",
    "switch_to_performance_model",
    "manage_code_channel",
    "manage_incident",
    "manage_thread",
    "merge_expedited_pr",
    "open_pull_request",
    "output_iframe",
    "publish_review",
    "read_only_sql",
    "read_store_item",
    "read_repo_file",
    "read_user_settings",
    "record_human_input",
    "recreate_sandbox",
    "refresh_workspace_start",
    "configure_repository",
    "report_platform_issue",
    "request_human_review",
    "request_pr_review",
    "request_service_connection",
    "reply_to_finding_thread",
    "resolve_finding_thread",
    "publish_workspace",
    "save_organization_skill",
    "delete_organization_skill",
    "save_plan",
    "save_user_instructions",
    "save_user_settings",
    "save_user_skill",
    "delete_user_skill",
    "schedule_thread_wakeup",
    "search_pull_requests",
    "search_repo_code",
    "start_thread",
    "slack_add_reaction",
    "slack_attach_html",
    "slack_list_channel_members",
    "slack_list_channels",
    "slack_move_thread",
    "slack_no_reply_needed",
    "slack_post_message",
    "slack_read_channel_messages",
    "slack_read_thread_messages",
    "slack_reply",
    "slack_breakout_thread",
    "submit_thread_feedback",
    "suggest_task",
    "trigger_automation",
    "update_automation",
    "update_finding",
    "web_search",
]

if TYPE_CHECKING:
    from openswe.github.tools.read_repo_file import read_repo_file
    from openswe.github.tools.search_repo_code import search_repo_code
    from openswe.incidents.tools import manage_incident
    from openswe.slack.tools.add_reaction import slack_add_reaction
    from openswe.slack.tools.attach_html import slack_attach_html
    from openswe.slack.tools.channels import (
        slack_list_channel_members,
        slack_list_channels,
        slack_post_message,
    )
    from openswe.slack.tools.manage_code_channel import manage_code_channel
    from openswe.slack.tools.move_thread import slack_move_thread
    from openswe.slack.tools.no_reply_needed import slack_no_reply_needed
    from openswe.slack.tools.read_channel_messages import slack_read_channel_messages
    from openswe.slack.tools.read_thread_messages import slack_read_thread_messages
    from openswe.slack.tools.reply import slack_reply
    from openswe.slack.tools.request_pr_review import request_pr_review
    from openswe.slack.tools.start_new_thread import slack_breakout_thread
    from openswe.tools.add_finding import add_finding
    from openswe.tools.automations import (
        create_automation,
        delete_automation,
        list_automations,
        trigger_automation,
        update_automation,
    )
    from openswe.tools.background_execute import background_execute
    from openswe.tools.background_task import background_task
    from openswe.tools.create_sandbox_file_download_url import create_sandbox_file_download_url
    from openswe.tools.expedite_pr_approval import expedite_pr_approval
    from openswe.tools.expose_port import expose_port
    from openswe.tools.fetch_review_diff import fetch_review_diff
    from openswe.tools.fetch_url import fetch_url
    from openswe.tools.http_request import http_request
    from openswe.tools.list_findings import list_findings
    from openswe.tools.list_review_findings import list_review_findings
    from openswe.tools.listen_events import list_event_types, listen_events
    from openswe.tools.manage_baby_sit import manage_baby_sit
    from openswe.tools.merge_expedited_pr import merge_expedited_pr
    from openswe.tools.open_pull_request import link_pull_request, open_pull_request
    from openswe.tools.organization_skills import delete_organization_skill, save_organization_skill
    from openswe.tools.output_iframe import output_iframe
    from openswe.tools.publish_review import publish_review
    from openswe.tools.read_only_sql import read_only_sql
    from openswe.tools.read_store_item import read_store_item
    from openswe.tools.read_user_settings import read_user_settings
    from openswe.tools.record_human_input import record_human_input
    from openswe.tools.recreate_sandbox import recreate_sandbox
    from openswe.tools.reply_to_finding_thread import reply_to_finding_thread
    from openswe.tools.report_platform_issue import report_platform_issue
    from openswe.tools.request_human_review import (
        assign_human_reviewer,
        auto_assign_human_reviewer,
        dismiss_human_review_request,
        request_human_review,
    )
    from openswe.tools.request_service_connection import request_service_connection
    from openswe.tools.resolve_finding_thread import resolve_finding_thread
    from openswe.tools.save_plan import save_plan
    from openswe.tools.save_user_instructions import save_user_instructions
    from openswe.tools.save_user_settings import save_user_settings
    from openswe.tools.schedule_thread_wakeup import schedule_thread_wakeup
    from openswe.tools.search_pull_requests import search_pull_requests
    from openswe.tools.submit_thread_feedback import submit_thread_feedback
    from openswe.tools.suggest_task import suggest_task
    from openswe.tools.switch_to_performance_model import switch_to_performance_model
    from openswe.tools.threads import get_thread, list_threads, manage_thread, start_thread
    from openswe.tools.update_finding import update_finding
    from openswe.tools.user_skills import delete_user_skill, save_user_skill
    from openswe.tools.web_search import web_search
    from openswe.tools.workspaces import (
        configure_repository,
        delete_workspace,
        list_workspaces,
        publish_workspace,
        refresh_workspace_start,
    )


def _load_export(name: str) -> Any:
    module_name = _TOOL_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    package = None if module_name.startswith("openswe.") else __name__
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
