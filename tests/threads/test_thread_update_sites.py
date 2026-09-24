"""Pins the set of modules that still write LangGraph thread metadata directly.

``agent.utils.thread_ops.update_thread_metadata`` keeps ``thread_index`` and the
transcript mirror in step with every write; a direct ``threads.update`` leaves
the sidebar stale until the reconciler's next tick. This list only shrinks.
"""

import re
from pathlib import Path

_AGENT = Path(__file__).resolve().parents[2] / "agent"
_DIRECT_UPDATE = re.compile(r"\.threads\.update\(")

_ALLOWED = frozenset(
    {
        "agent/utils/thread_ops.py",
        "agent/github/webhook.py",
        "agent/incidents/channels.py",
        "agent/middleware/model_errors.py",
        "agent/review/chat.py",
        "agent/review/findings.py",
        "agent/review/session.py",
        "agent/sandboxes/lifecycle.py",
        "agent/server.py",
        "agent/slack/move.py",
        "agent/slack/orphan.py",
        "agent/slack/stop.py",
        "agent/slack/tools/manage_code_channel.py",
        "agent/slack/tools/start_new_thread.py",
        "agent/slack/webhook.py",
        "agent/thread_feedback.py",
        "agent/threads/plan_store.py",
        "agent/threads/proxy.py",
        "agent/threads/workflow_approval.py",
        "agent/tools/notify_automation_channel.py",
        "agent/tools/schedule_thread_wakeup.py",
        "agent/utils/background_task_state.py",
        "agent/utils/thread_settings.py",
    }
)


def test_no_new_direct_thread_metadata_writes() -> None:
    direct = {
        path.relative_to(_AGENT.parent).as_posix()
        for path in _AGENT.rglob("*.py")
        if _DIRECT_UPDATE.search(path.read_text(encoding="utf-8"))
    }
    added = direct - _ALLOWED
    assert not added, (
        f"Direct `threads.update(` calls in {sorted(added)}: use "
        "`agent.utils.thread_ops.update_thread_metadata` so the thread index stays in sync."
    )
    removed = _ALLOWED - direct
    assert not removed, f"{sorted(removed)} no longer write directly; drop them from _ALLOWED."
