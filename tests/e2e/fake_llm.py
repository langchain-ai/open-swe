"""A scripted fake chat model — the ONLY faked piece of the agent.

It drives the real deepagents loop with a fixed sequence of tool calls that
implement a tiny feature, push a branch to the fake-GitHub remote, open a PR via
the real ``open_pull_request`` tool, and post the result back with the real
``slack_thread_reply`` tool. The final Slack step reads the actual PR URL out of
the preceding tool result, exactly as a real model would.
"""

import json
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
    SECOND_FEATURE_BRANCH,
    SECOND_OWNER,
    SECOND_PR_TITLE,
    SECOND_REPO,
)
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, ModelProfile
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult

_SETUP_SCRIPT = f"""
set -e
rm -rf repo
git clone "$E2E_REMOTE" repo
cd repo
git config user.email "dev@example.com"
git config user.name "Dev User"
git checkout -b {FEATURE_BRANCH}
cat > {FEATURE_FILE} <<'EOF'
def normalize(name):
    return name.strip()

def greet(name):
    return "Hello!"

def farewell(name):
    return f"Goodbye, {{name}}!"
EOF
""".strip()

_COMMIT_SCRIPT = f"""
set -e
cd repo
git add -A
git commit -m "{PR_TITLE}"
git push origin {FEATURE_BRANCH}
echo PUSHED_OK
""".strip()

_MULTI_REPO_SCRIPT = f"""
set -e
rm -rf repo companion
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
cd ..
git clone "$E2E_SECOND_REMOTE" companion
cd companion
git config user.email "dev@example.com"
git config user.name "Dev User"
git checkout -b {SECOND_FEATURE_BRANCH}
cat > integration.py <<'EOF'
def connect():
    return "connected"
EOF
git add -A
git commit -m "{SECOND_PR_TITLE}"
git push origin {SECOND_FEATURE_BRANCH}
echo BOTH_PUSHED_OK
""".strip()

_DESKTOP_PR_PAYLOAD = json.dumps(
    {
        "head": FEATURE_BRANCH,
        "base": BASE_BRANCH,
        "title": PR_TITLE,
        "body": "Adds a `greet()` helper as requested.",
        "draft": True,
    }
)
_DESKTOP_IMPLEMENT_SCRIPT = f"""
set -e
git checkout -b {FEATURE_BRANCH}
cat > {FEATURE_FILE} <<'EOF'
def greet(name):
    return f"Hello, {{name}}!"
EOF
git add {FEATURE_FILE}
git -c "user.email=dev@example.com" -c "user.name=Dev User" commit -m "{PR_TITLE}"
git push origin {FEATURE_BRANCH}
curl --fail --silent --show-error \
  --request POST \
  --header 'content-type: application/json' \
  --data '{_DESKTOP_PR_PAYLOAD}' \
  "$E2E_FAKE_GITHUB_API/repos/{OWNER}/{REPO}/pulls"
""".strip()

# The system prompt of the most recent model call, so specs can assert what the
# agent was actually told (e.g. the environment section) rather than infer it.
LAST_SYSTEM_PROMPT: dict[str, str] = {"text": ""}

_BUSY_HOLD_RE = re.compile(r"E2E_BUSY_HOLD(?::(\d+(?:\.\d+)?))?")
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


def _parallel_tool_step(content: str, calls: tuple[ToolCallSpec, ...]) -> StepSpec:
    return StepSpec(content=content, tool_calls=calls)


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
        response_metadata={"model_name": "fake-scripted-model"},
        usage_metadata={
            "input_tokens": 12_000,
            "output_tokens": 345,
            "total_tokens": 12_345,
        },
    )


def _multi_pr_reply_step(messages: list[BaseMessage]) -> AIMessage:
    url = _pr_url_from_messages(messages) or "(PR url unavailable)"
    return AIMessage(
        content="Replying in the Slack thread with the cross-repository PRs.",
        tool_calls=[
            {
                "name": "slack_thread_reply",
                "args": {
                    "message": (
                        f"Opened pull requests in `{OWNER}/{REPO}` and "
                        f"`{SECOND_OWNER}/{SECOND_REPO}`; latest: <{url}|{SECOND_PR_TITLE}>."
                    )
                },
                "id": "call-multi-pr-reply",
            }
        ],
    )


def _desktop_reply_step(messages: list[BaseMessage]) -> AIMessage:
    url = _pr_url_from_messages(messages) or "(PR url unavailable)"
    return AIMessage(
        content=(
            f"Done! I added `{FEATURE_FILE}` and opened [{PR_TITLE}]({url}) on the fake GitHub."
        )
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
                    "Take a look, leave comments, and choose what to do next.",
                    "options": ["Approve & implement", "Request changes"],
                },
                "id": "call-plan-done",
            }
        ],
    )


ENVIRONMENT_NAME = "default"
ENVIRONMENT_PROMPT = (
    "Checkouts live in /workspace/repos. Build with `make build`, test with `make test`."
)
ENVIRONMENT_PROVISION_SCRIPT = "mkdir -p repos && echo provisioned > repos/.provisioned && ls repos"

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


SUBAGENT_TASKS: dict[str, str] = {
    "scout": "Scout the repository layout and report which files define the greet helper.",
    "auditor": "Audit the repository for missing tests and report what is uncovered.",
}

SCOUT_RESULT = "Scout done: greet() lives in the feature module; no other definitions found."
AUDITOR_RESULT = "Audit done: the greet() helper has no test coverage."

# Each nested `execute` sleeps so the parent's card is observably `running`
# before it settles — the transition is what the E2E asserts on.
_SCOUT_SCRIPT = """
set -e
sleep 15
echo SCOUTED
""".strip()

_AUDIT_SCRIPT = """
set -e
sleep 25
echo AUDITED
""".strip()


SCRIPT_LIBRARY: dict[str, tuple[StepSpec, ...]] = {
    "subagents": (
        _tool_step(
            "Acknowledging before fanning out.",
            "slack_thread_reply",
            {"message": "On it — fanning out to a couple of subagents."},
            "call-subagents-ack",
        ),
        _parallel_tool_step(
            "Delegating the scouting and the audit in parallel.",
            (
                _tool_call(
                    "task",
                    {
                        "subagent_type": "general-purpose",
                        "description": SUBAGENT_TASKS["scout"],
                    },
                    "call-task-scout",
                ),
                _tool_call(
                    "task",
                    {
                        "subagent_type": "general-purpose",
                        "description": SUBAGENT_TASKS["auditor"],
                    },
                    "call-task-auditor",
                ),
            ),
        ),
        _tool_step(
            "Reporting what the subagents found.",
            "slack_thread_reply",
            {"message": "Both subagents finished — the greet() helper is untested."},
            "call-subagents-reply",
        ),
    ),
    "subagent:scout": (
        _tool_step(
            "Listing the repository.",
            "execute",
            {"command": _SCOUT_SCRIPT},
            "call-scout-ls",
        ),
        _tool_step(
            "Grepping for the helper.",
            "execute",
            {"command": "grep -r greet . || true"},
            "call-scout-grep",
        ),
        StepSpec(content=SCOUT_RESULT),
    ),
    "subagent:auditor": (
        _tool_step(
            "Looking for tests.",
            "execute",
            {"command": _AUDIT_SCRIPT},
            "call-audit-find",
        ),
        StepSpec(content=AUDITOR_RESULT),
    ),
    "desktop": (
        _tool_step(
            "Implementing the change in the selected local project.",
            "execute",
            {"command": _DESKTOP_IMPLEMENT_SCRIPT},
            "call-desktop-impl",
        ),
        _dynamic_step(_desktop_reply_step),
    ),
    "implement": (
        _tool_step(
            "Acknowledging the Slack request before starting work.",
            "slack_thread_reply",
            {"message": "On it!"},
            "call-ack",
        ),
        _tool_step(
            "Setting up the repository.",
            "execute",
            {"command": _SETUP_SCRIPT},
            "call-setup",
        ),
        _tool_step(
            "Implementing the greeting.",
            "edit_file",
            {
                "file_path": f"/repo/{FEATURE_FILE}",
                "old_string": 'def greet(name):\n    return "Hello!"',
                "new_string": 'def greet(name):\n    return f"Hello, {name}!"',
            },
            "call-edit",
        ),
        _tool_step(
            "Committing and pushing the change.",
            "execute",
            {"command": _COMMIT_SCRIPT},
            "call-commit",
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
    "multi_pr": (
        _tool_step(
            "Acknowledging the cross-repository request before starting work.",
            "slack_thread_reply",
            {"message": "On it!"},
            "call-multi-ack",
        ),
        _tool_step(
            "Implementing and pushing both repository changes.",
            "execute",
            {"command": _MULTI_REPO_SCRIPT},
            "call-multi-repos",
        ),
        _tool_step(
            "Opening the primary pull request.",
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
            "call-multi-first-pr",
        ),
        _tool_step(
            "Opening the companion pull request.",
            "open_pull_request",
            {
                "owner": SECOND_OWNER,
                "repo": SECOND_REPO,
                "head": SECOND_FEATURE_BRANCH,
                "base": BASE_BRANCH,
                "title": SECOND_PR_TITLE,
                "body": "Adds the companion integration as requested.",
                "draft": False,
            },
            "call-multi-second-pr",
        ),
        _dynamic_step(_multi_pr_reply_step),
    ),
    "move": (
        _tool_step(
            "Moving the current Open SWE thread to another Slack channel.",
            "slack_move_thread",
            {
                "message": "Continue the existing Open SWE task in this thread.",
                "channel_id": "C_TARGET",
            },
            "call-move",
        ),
        _tool_step(
            "Confirming the moved thread in its new location.",
            "slack_thread_reply",
            {"message": "Moved this Open SWE thread and preserved its state."},
            "call-move-reply",
        ),
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
    "environment": (
        _tool_step(
            "Provisioning this sandbox before capturing it.",
            "execute",
            {"command": ENVIRONMENT_PROVISION_SCRIPT},
            "call-env-provision",
        ),
        _tool_step(
            "Saving the environment record.",
            "save_environment",
            {
                "name": ENVIRONMENT_NAME,
                "prompt": ENVIRONMENT_PROMPT,
                "repos": [f"{OWNER}/{REPO}"],
            },
            "call-env-save",
        ),
        _tool_step(
            "Capturing this sandbox as the environment snapshot.",
            "capture_environment_snapshot",
            {"name": ENVIRONMENT_NAME},
            "call-env-capture",
        ),
        StepSpec(content=f"The `{ENVIRONMENT_NAME}` environment is captured and live."),
    ),
    "followup": (_dynamic_step(_followup_step),),
}


def _subagent_script_name(first_text: str) -> str | None:
    """Route a nested loop to its script by the task description it was handed."""
    for name, description in SUBAGENT_TASKS.items():
        if description in first_text:
            return f"subagent:{name}"
    return None


def _is_subagent_request(text: str) -> bool:
    t = text.lower()
    return "fan out" in t or "subagent" in t


def _is_plan_request(text: str) -> bool:
    return "plan" in text.lower()


def _is_environment_request(text: str) -> bool:
    return "environment" in text.lower()


def _is_breakout_request(text: str) -> bool:
    t = text.lower()
    return "break out" in t or "separate thread" in t or "split out" in t


def _is_move_request(text: str) -> bool:
    return "E2E_MOVE_THREAD" in text


def _is_move_followup(text: str) -> bool:
    return "E2E_DESTINATION_FOLLOWUP" in text or "E2E_SOURCE_RETAG" in text


def _is_approval(text: str) -> bool:
    t = text.lower()
    return "approved" in t and "implement" in t


def _is_revision(text: str) -> bool:
    t = text.lower()
    return "needs changes" in t or "publish an updated plan" in t


SCRIPT_RULES: tuple[ScriptRule, ...] = (
    ScriptRule(
        "subagents", lambda ctx: ctx.human_count <= 1 and _is_subagent_request(ctx.first_text)
    ),
    ScriptRule(
        "desktop",
        lambda ctx: ctx.human_count <= 1 and "E2E_DESKTOP_LOCAL" in ctx.first_text,
    ),
    ScriptRule(
        "environment", lambda ctx: ctx.human_count <= 1 and _is_environment_request(ctx.first_text)
    ),
    ScriptRule("followup", lambda ctx: _is_move_followup(ctx.last_text)),
    ScriptRule("move", lambda ctx: _is_move_request(ctx.first_text)),
    ScriptRule("implement", lambda ctx: _is_approval(ctx.last_text)),
    ScriptRule("plan", lambda ctx: _is_revision(ctx.last_text)),
    ScriptRule("plan", lambda ctx: ctx.human_count <= 1 and _is_plan_request(ctx.first_text)),
    ScriptRule(
        "breakout", lambda ctx: ctx.human_count <= 1 and _is_breakout_request(ctx.first_text)
    ),
    ScriptRule(
        "multi_pr",
        lambda ctx: ctx.human_count <= 1 and "E2E_MULTI_PR" in f"{ctx.first_text}\n{ctx.last_text}",
    ),
    ScriptRule("implement", lambda ctx: ctx.human_count <= 1),
    ScriptRule("followup", lambda _ctx: True),
)


def _script_for(context: ScriptContext) -> tuple[StepSpec, ...]:
    nested = _subagent_script_name(context.first_text)
    if nested is not None:
        return SCRIPT_LIBRARY[nested]
    for rule in SCRIPT_RULES:
        if rule.predicate(context):
            return SCRIPT_LIBRARY[rule.name]
    return SCRIPT_LIBRARY["followup"]


def build_script() -> list[StepSpec]:
    return list(SCRIPT_LIBRARY["implement"])


class FakeScriptedChatModel(BaseChatModel):
    """Returns the next scripted AIMessage based on how far the loop has run."""

    model: str = "fake"
    profile: ModelProfile | None = {
        "tool_calling": True,
        "max_input_tokens": 128_000,
    }
    script: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "fake-scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "FakeScriptedChatModel":  # noqa: ARG002
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,  # noqa: ARG002
        run_manager: CallbackManagerForLLMRun | None = None,  # noqa: ARG002
        **kwargs: Any,
    ) -> ChatResult:
        for message in messages:
            if isinstance(message, SystemMessage):
                LAST_SYSTEM_PROMPT["text"] = _text(message.content)
                break
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
        # marker. The block lands on the second model call so the Slack
        # acknowledgement — and the web link a spec may need to click — is already
        # out by the time the run stalls. `E2E_BUSY_HOLD:<n>` overrides the window
        # so a spec needing a short hold does not leave a run in flight for the
        # specs that follow it.
        hold = _BUSY_HOLD_RE.search(context.last_text) if step_index == 1 else None
        if hold:
            time.sleep(float(hold.group(1) or os.environ.get("E2E_BUSY_HOLD_SECONDS", "10")))
        step = script[step_index] if step_index < len(script) else SCRIPT_LIBRARY["followup"][0]
        return ChatResult(generations=[ChatGeneration(message=_render_step(step, messages))])
