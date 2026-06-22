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


def _pr_url_from_messages(messages: list[BaseMessage]) -> str | None:
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            text = msg.content if isinstance(msg.content, str) else str(msg.content)
            match = re.search(r"https?://[^\s\"']+/pull/\d+", text)
            if match:
                return match.group(0)
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
    text = (
        f"✅ Done! I implemented the change and opened a PR: <{url}|{PR_TITLE}>\n\n"
        f"• Added `{FEATURE_FILE}` with a `greet()` helper.\n"
        "Let me know if you'd like any changes."
    )
    return AIMessage(
        content="Replying in the Slack thread with the PR link.",
        tool_calls=[{"name": "slack_thread_reply", "args": {"message": text}, "id": "call-reply"}],
    )


FOLLOW_UP_REPLY = "Thanks! The PR is ready for review — anything else you'd like changed?"


def _step_followup(_messages: list[BaseMessage]) -> AIMessage:
    # A web/Slack follow-up after the PR exists: a plain reply, no new PR. Its
    # content lands in the thread transcript the dashboard renders.
    return AIMessage(content=FOLLOW_UP_REPLY)


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
        # First human turn implements + opens the PR; later turns (a web/Slack
        # follow-up on the same thread) just reply.
        human_turns = sum(1 for m in messages if isinstance(m, HumanMessage))
        script = build_script() if human_turns <= 1 else build_followup_script()

        # Step within the *current* turn: AIMessages since the last human turn.
        # (Counting the whole thread would short-circuit reused/multi-turn threads.)
        last_human = max(
            (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1
        )
        step = sum(1 for m in messages[last_human + 1 :] if isinstance(m, AIMessage))
        if step < len(script):
            message = script[step](messages)
        else:
            message = AIMessage(content="All set — the PR is open and linked in the thread.")
        return ChatResult(generations=[ChatGeneration(message=message)])
