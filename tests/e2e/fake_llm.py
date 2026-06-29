"""A scripted fake chat model — the ONLY faked piece of the agent.

It drives the real deepagents loop with a fixed sequence of tool calls that
implement a tiny feature, push a branch to the fake-GitHub remote, open a PR via
the real ``open_pull_request`` tool, and post the result back with the real
``slack_thread_reply`` tool. The final Slack step reads the actual PR URL out of
the preceding tool result, exactly as a real model would.
"""

from __future__ import annotations

import re
from typing import Any

from e2e_env import (
    BASE_BRANCH,
    FEATURE_BRANCH,
    FEATURE_FILE,
    OWNER,
    PR_TITLE,
    REPO,
)
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

# One shell command that does the whole git workflow. Each execute() runs in a
# fresh shell rooted at the sandbox dir, so the clone+commit+push is bundled.
_IMPLEMENT_SCRIPT = f"""
set -e
rm -rf repo
git clone "$E2E_REMOTE" repo
cd repo
git config user.email "dev@example.com"
git config user.name "Dev User"
git checkout -b {FEATURE_BRANCH}
cat > {FEATURE_FILE} <<'EOF'
def greet(name):
    return f"Hello, {{name}}!"
EOF
git add -A
git commit -m "{PR_TITLE}"
git push origin {FEATURE_BRANCH}
echo PUSHED_OK
""".strip()


_PLAN_URL_RE = re.compile(r"https?://[^\s\"'<>)\]|]+/plan\b")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)


def _pr_url_from_messages(messages: list[BaseMessage]) -> str | None:
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            text = msg.content if isinstance(msg.content, str) else str(msg.content)
            match = re.search(r"https?://[^\s\"']+/pull/\d+", text)
            if match:
                return match.group(0)
    return None


def _plan_url_from_messages(messages: list[BaseMessage]) -> str | None:
    """The plan-review URL is injected into the system prompt; a real model would
    read it the same way."""
    for msg in messages:
        match = _PLAN_URL_RE.search(_text(msg.content))
        if match:
            return match.group(0).rstrip(".,")
    return None


def _reviewer_feedback(messages: list[BaseMessage]) -> str | None:
    """The harvested reviewer comments the backend hands the agent on approval."""
    humans = [m for m in messages if isinstance(m, HumanMessage)]
    if not humans:
        return None
    text = _text(humans[-1].content)
    idx = text.lower().find("feedback")
    if "approved" in text.lower() and idx != -1:
        return text[idx:].strip()
    return None


def _step_implement(_messages: list[BaseMessage]) -> AIMessage:
    return AIMessage(
        content="Setting up the repo and implementing the change.",
        tool_calls=[{"name": "execute", "args": {"command": _IMPLEMENT_SCRIPT}, "id": "call-impl"}],
    )


def _step_open_pr(_messages: list[BaseMessage]) -> AIMessage:
    return AIMessage(
        content="Opening a pull request.",
        tool_calls=[
            {
                "name": "open_pull_request",
                "args": {
                    "owner": OWNER,
                    "repo": REPO,
                    "head": FEATURE_BRANCH,
                    "base": BASE_BRANCH,
                    "title": PR_TITLE,
                    "body": "Adds a `greet()` helper as requested.",
                    "draft": True,
                },
                "id": "call-pr",
            }
        ],
    )


def _step_reply(messages: list[BaseMessage]) -> AIMessage:
    url = _pr_url_from_messages(messages) or "(PR url unavailable)"
    feedback = _reviewer_feedback(messages)
    extra = f"\n\nReviewer feedback I addressed:\n{feedback}" if feedback else ""
    text = (
        f"✅ Done! I implemented the change and opened a PR: <{url}|{PR_TITLE}>\n\n"
        f"• Added `{FEATURE_FILE}` with a `greet()` helper.{extra}\n"
        "Let me know if you'd like any changes."
    )
    return AIMessage(
        content="Replying in the Slack thread with the PR link.",
        tool_calls=[{"name": "slack_thread_reply", "args": {"message": text}, "id": "call-reply"}],
    )


# --- plan-mode flow --------------------------------------------------------
PLAN_MARKDOWN = """## Plan: Add greet() helper

### Overview
Add a tiny greeting helper to the demo repo.

### Files to change
- `greet.py` — new module exposing a `greet(name)` function.

### Steps
1. Create `greet.py` with a `greet(name)` function.
2. Open a draft PR with the change.

### Verification
- Import `greet` and confirm it returns the expected string.
"""


def _step_enter_plan(_messages: list[BaseMessage]) -> AIMessage:
    return AIMessage(
        content="This is worth planning first — entering plan mode.",
        tool_calls=[{"name": "enter_plan_mode", "args": {}, "id": "call-enter-plan"}],
    )


def _step_plan_link(messages: list[BaseMessage]) -> AIMessage:
    url = _plan_url_from_messages(messages) or "(plan link unavailable)"
    return AIMessage(
        content="Sharing the plan-review link.",
        tool_calls=[
            {
                "name": "slack_thread_reply",
                "args": {
                    "message": f"I'm putting together a plan. Follow along and review it here: <{url}|plan review>"
                },
                "id": "call-plan-link",
            }
        ],
    )


def _step_plan_research(_messages: list[BaseMessage]) -> AIMessage:
    return AIMessage(
        content="Reading the repo to ground the plan.",
        tool_calls=[
            {"name": "execute", "args": {"command": "echo planning && ls"}, "id": "call-plan-read"}
        ],
    )


def _step_write_plan(_messages: list[BaseMessage]) -> AIMessage:
    return AIMessage(
        content="Writing the plan file for review.",
        tool_calls=[
            {
                "name": "write_file",
                "args": {"file_path": "/workspace/plan.md", "content": PLAN_MARKDOWN},
                "id": "call-write-plan",
            }
        ],
    )


def _step_save_plan(_messages: list[BaseMessage]) -> AIMessage:
    return AIMessage(
        content="Saving the plan for review.",
        tool_calls=[
            {
                "name": "save_plan",
                "args": {"plan_file_path": "/workspace/plan.md"},
                "id": "call-save-plan",
            }
        ],
    )


def _step_plan_complete(messages: list[BaseMessage]) -> AIMessage:
    url = _plan_url_from_messages(messages) or "(plan link unavailable)"
    return AIMessage(
        content="Announcing the plan is ready.",
        tool_calls=[
            {
                "name": "slack_thread_reply",
                "args": {
                    "message": f"✅ The plan is ready for review: <{url}|open the plan>. "
                    "Take a look, leave comments, and approve it when you're happy."
                },
                "id": "call-plan-done",
            }
        ],
    )


def _step_plan_end(_messages: list[BaseMessage]) -> AIMessage:
    return AIMessage(content="I'll wait for your review and approval before implementing.")


def build_plan_script() -> list[Any]:
    return [
        _step_enter_plan,
        _step_plan_link,
        _step_plan_research,
        _step_write_plan,
        _step_save_plan,
        _step_plan_complete,
        _step_plan_end,
    ]


FOLLOW_UP_REPLY = "Thanks! The PR is ready for review — anything else you'd like changed?"
_ATTRIBUTION_RE = re.compile(r"@([A-Za-z0-9-]+):")


def _latest_attribution(messages: list[BaseMessage]) -> str | None:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            match = _ATTRIBUTION_RE.search(_text(msg.content))
            if match:
                return f"@{match.group(1)}"
    return None


def _step_followup(messages: list[BaseMessage]) -> AIMessage:
    attribution = _latest_attribution(messages)
    suffix = f" I saw this follow-up was from {attribution}." if attribution else ""
    return AIMessage(content=f"{FOLLOW_UP_REPLY}{suffix}")


def build_script() -> list[Any]:
    return [_step_implement, _step_open_pr, _step_reply]


def build_followup_script() -> list[Any]:
    return [_step_followup]


class FakeScriptedChatModel(BaseChatModel):
    """Returns the next scripted AIMessage based on how far the loop has run."""

    script: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "fake-scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeScriptedChatModel:  # noqa: ARG002
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,  # noqa: ARG002
        run_manager: CallbackManagerForLLMRun | None = None,  # noqa: ARG002
        **kwargs: Any,
    ) -> ChatResult:
        humans = [m for m in messages if isinstance(m, HumanMessage)]
        first_text = _text(humans[0].content) if humans else ""
        last_text = _text(humans[-1].content) if humans else ""

        # Pick the script for the current turn by what the latest human asked.
        if _is_approval(last_text):
            script = build_script()  # implement + open PR + reply
        elif _is_revision(last_text):
            script = build_plan_script()  # re-plan after requested changes
        elif _is_plan_request(first_text) and len(humans) <= 1:
            script = build_plan_script()  # first ask was to plan
        elif len(humans) <= 1:
            script = build_script()
        else:
            script = build_followup_script()

        # Step within the *current* turn: AIMessages since the last human turn.
        last_human = max(
            (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1
        )
        step = sum(1 for m in messages[last_human + 1 :] if isinstance(m, AIMessage))
        if step < len(script):
            message = script[step](messages)
        else:
            message = _step_followup(messages)
        return ChatResult(generations=[ChatGeneration(message=message)])


def _is_plan_request(text: str) -> bool:
    return "plan" in text.lower()


def _is_approval(text: str) -> bool:
    t = text.lower()
    return "approved" in t and "implement" in t


def _is_revision(text: str) -> bool:
    t = text.lower()
    return "needs changes" in t or "publish an updated plan" in t
