"""A scripted fake chat model — the ONLY faked piece of the agent.

It drives the real deepagents loop with a fixed sequence of tool calls that
implement a tiny feature, push a branch to the fake-GitHub remote, open a PR via
the real ``open_pull_request`` tool, and post the result back with the real
``slack_thread_reply`` tool. The final Slack step reads the actual PR URL out of
the preceding tool result, exactly as a real model would.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
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
_ATTRIBUTION_RE = re.compile(r"@([A-Za-z0-9-]+):")

ToolArgs = dict[str, Any]
StepFactory = Callable[[list[BaseMessage]], AIMessage]
ScriptPredicate = Callable[["ScriptContext"], bool]


@dataclass(frozen=True)
class ToolCallSpec:
    name: str
    args: ToolArgs
    call_id: str


@dataclass(frozen=True)
class StepSpec:
    content: str = ""
    tool_calls: tuple[ToolCallSpec, ...] = ()
    factory: StepFactory | None = None


@dataclass(frozen=True)
class ScriptContext:
    first_text: str
    last_text: str
    human_count: int


@dataclass(frozen=True)
class ScriptRule:
    name: str
    predicate: ScriptPredicate


def _tool_call(name: str, args: ToolArgs, call_id: str) -> ToolCallSpec:
    return ToolCallSpec(name=name, args=args, call_id=call_id)


def _tool_step(content: str, name: str, args: ToolArgs, call_id: str) -> StepSpec:
    return StepSpec(content=content, tool_calls=(_tool_call(name, args, call_id),))


def _dynamic_step(factory: StepFactory) -> StepSpec:
    return StepSpec(factory=factory)


def _render_step(step: StepSpec, messages: list[BaseMessage]) -> AIMessage:
    if step.factory is not None:
        return step.factory(messages)
    return AIMessage(
        content=step.content,
        tool_calls=[
            {"name": call.name, "args": dict(call.args), "id": call.call_id}
            for call in step.tool_calls
        ],
    )


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
    """The plan-review URL is injected into the system prompt; a real model would read it."""
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


def _reply_step(messages: list[BaseMessage]) -> AIMessage:
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


PLAN_FILE_PATH = "/workspace/plans/2026-06-29-greet-helper.md"

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


def _plan_link_step(messages: list[BaseMessage]) -> AIMessage:
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


def _plan_research_step(_messages: list[BaseMessage]) -> AIMessage:
    return AIMessage(
        content="Reading the repo to ground the plan.",
        tool_calls=[
            {"name": "execute", "args": {"command": "echo planning && ls"}, "id": "call-plan-read"}
        ],
    )


def _write_plan_step(_messages: list[BaseMessage]) -> AIMessage:
    return AIMessage(
        content="Writing the plan file for review.",
        tool_calls=[
            {
                "name": "write_file",
                "args": {"file_path": PLAN_FILE_PATH, "content": PLAN_MARKDOWN},
                "id": "call-write-plan",
            }
        ],
    )


def _save_plan_step(_messages: list[BaseMessage]) -> AIMessage:
    return AIMessage(
        content="Saving the plan for review.",
        tool_calls=[
            {
                "name": "save_plan",
                "args": {"plan_file_path": PLAN_FILE_PATH},
                "id": "call-save-plan",
            }
        ],
    )


def _plan_complete_step(messages: list[BaseMessage]) -> AIMessage:
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


FOLLOW_UP_REPLY = "Thanks! The PR is ready for review — anything else you'd like changed?"


def _latest_attribution(messages: list[BaseMessage]) -> str | None:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            match = _ATTRIBUTION_RE.search(_text(msg.content))
            if match:
                return f"@{match.group(1)}"
    return None


def _followup_step(messages: list[BaseMessage]) -> AIMessage:
    if any(
        isinstance(msg, HumanMessage) and "Please queue this follow-up" in _text(msg.content)
        for msg in messages
    ):
        time.sleep(2)
    attribution = _latest_attribution(messages)
    suffix = f" I saw this follow-up was from {attribution}." if attribution else ""
    return AIMessage(content=f"{FOLLOW_UP_REPLY}{suffix}")


SCRIPT_LIBRARY: dict[str, tuple[StepSpec, ...]] = {
    "implement": (
        _tool_step(
            "Acknowledging the Slack request before starting work.",
            "slack_thread_reply",
            {"message": "On it!"},
            "call-ack",
        ),
        _tool_step(
            "Setting up the repo and implementing the change.",
            "execute",
            {"command": _IMPLEMENT_SCRIPT},
            "call-impl",
        ),
        _tool_step(
            "Opening a pull request.",
            "open_pull_request",
            {
                "owner": OWNER,
                "repo": REPO,
                "head": FEATURE_BRANCH,
                "base": BASE_BRANCH,
                "title": PR_TITLE,
                "body": "Adds a `greet()` helper as requested.",
                "draft": True,
            },
            "call-pr",
        ),
        _dynamic_step(_reply_step),
    ),
    "breakout": (
        _tool_step(
            "Starting a separate Slack thread for the breakout task.",
            "slack_start_new_thread",
            {
                "title": "Add greet() helper",
                "instructions": "Please add a greet() helper and open a draft PR in the default repository. Use the current Slack request as context, and report progress in this new thread.",
            },
            "call-breakout",
        ),
        _tool_step(
            "Confirming the breakout thread was started.",
            "slack_thread_reply",
            {"message": "I started a separate Open SWE thread for that aspect."},
            "call-breakout-reply",
        ),
    ),
    "plan": (
        _tool_step(
            "This is worth planning first — entering plan mode.",
            "enter_plan_mode",
            {},
            "call-enter-plan",
        ),
        _dynamic_step(_plan_link_step),
        _dynamic_step(_plan_research_step),
        _dynamic_step(_write_plan_step),
        _dynamic_step(_save_plan_step),
        _dynamic_step(_plan_complete_step),
        StepSpec(content="I'll wait for your review and approval before implementing."),
    ),
    "followup": (_dynamic_step(_followup_step),),
}


def _is_plan_request(text: str) -> bool:
    return "plan" in text.lower()


def _is_breakout_request(text: str) -> bool:
    t = text.lower()
    return "break out" in t or "separate thread" in t or "split out" in t


def _is_approval(text: str) -> bool:
    t = text.lower()
    return "approved" in t and "implement" in t


def _is_revision(text: str) -> bool:
    t = text.lower()
    return "needs changes" in t or "publish an updated plan" in t


SCRIPT_RULES: tuple[ScriptRule, ...] = (
    ScriptRule("implement", lambda ctx: _is_approval(ctx.last_text)),
    ScriptRule("plan", lambda ctx: _is_revision(ctx.last_text)),
    ScriptRule("plan", lambda ctx: ctx.human_count <= 1 and _is_plan_request(ctx.first_text)),
    ScriptRule(
        "breakout", lambda ctx: ctx.human_count <= 1 and _is_breakout_request(ctx.first_text)
    ),
    ScriptRule("implement", lambda ctx: ctx.human_count <= 1),
    ScriptRule("followup", lambda _ctx: True),
)


def _script_for(context: ScriptContext) -> tuple[StepSpec, ...]:
    for rule in SCRIPT_RULES:
        if rule.predicate(context):
            return SCRIPT_LIBRARY[rule.name]
    return SCRIPT_LIBRARY["followup"]


def build_script() -> list[StepSpec]:
    return list(SCRIPT_LIBRARY["implement"])


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
        context = ScriptContext(
            first_text=_text(humans[0].content) if humans else "",
            last_text=_text(humans[-1].content) if humans else "",
            human_count=len(humans),
        )
        script = _script_for(context)

        last_human = max(
            (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1
        )
        step_index = sum(1 for m in messages[last_human + 1 :] if isinstance(m, AIMessage))

        # Keep a run busy on demand so E2E can land follow-ups mid-run (exercising
        # the interrupt-debounce path). Only the triggering message carries the
        # marker, and only the first model call of that run blocks.
        if step_index == 0 and "E2E_BUSY_HOLD" in context.last_text:
            time.sleep(float(os.environ.get("E2E_BUSY_HOLD_SECONDS", "10")))
        step = script[step_index] if step_index < len(script) else SCRIPT_LIBRARY["followup"][0]
        return ChatResult(generations=[ChatGeneration(message=_render_step(step, messages))])
