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
    DEMO_CHANNEL,
    FAKE_GITHUB_API,
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

from agent.input_messages import TURN_ANNOTATION_SENDER_IDS


def _slack_thread_ts(messages: list[BaseMessage]) -> str:
    matches = re.findall(r"Thread TS: ([0-9.]+)", "\n".join(_text(m.content) for m in messages))
    return matches[-1] if matches else ""


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

# The expedited-review flow needs a change small enough to qualify (at most ten
# changed lines, no protected paths), so it touches one line of one file.
EXPEDITE_MARKER = "E2E_EXPEDITE"
EXPEDITE_NOW_MARKER = "E2E_EXPEDITE_NOW"
EXPEDITE_PR_TITLE = "Fix the greeting punctuation"

# The seeded remote holds only a README, so the first turn writes the file. Two
# added lines keeps the pull request inside the ten-line eligibility limit.
_EXPEDITE_SETUP_SCRIPT = f"""
set -e
rm -rf repo
git clone "$E2E_REMOTE" repo
cd repo
git config user.email "dev@example.com"
git config user.name "Dev User"
git checkout -b {FEATURE_BRANCH}
cat > {FEATURE_FILE} <<'EOF'
def greet(name):
    return "Hello!!"
EOF
git add -A
git commit -m "{EXPEDITE_PR_TITLE}"
git push origin {FEATURE_BRANCH}
echo PUSHED_OK
""".strip()

_EXPEDITE_FIX_SCRIPT = f"""
set -e
cd repo
cat > {FEATURE_FILE} <<'EOF'
def greet(name):
    return "Hello!"
EOF
git add -A
git commit -m "Restore the expected greeting"
git push origin {FEATURE_BRANCH}
echo FIXED_OK
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

_MANY_FILES_IMPLEMENT_SCRIPT = f"""
set -e
cd repo
git config user.email "dev@example.com"
git config user.name "Dev User"
git checkout -b {FEATURE_BRANCH}
for index in $(seq -w 1 15); do
  printf 'change %s\n' "$index" > "change-$index.txt"
done
git add -A
git commit -m "{PR_TITLE}"
git push origin {FEATURE_BRANCH}
echo PUSHED_OK
""".strip()

_IFRAME_HTML_PATH = "/workspace/iframe-output.html"
_IFRAME_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Iframe E2E Preview</title>
  <style>body { min-height: 420px; margin: 0; color: rebeccapurple; }</style>
</head>
<body>
  <main>
    <h1>Iframe preview</h1>
    <p id="output-data">Prototype loaded</p>
  </main>
</body>
</html>
"""

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
  "{FAKE_GITHUB_API}/repos/{OWNER}/{REPO}/pulls"
""".strip()

# The system prompt of the most recent model call, so specs can assert what the
# agent was actually told (e.g. the workspace section) rather than infer it.
LAST_SYSTEM_PROMPT: dict[str, str] = {"text": ""}

_BUSY_HOLD_RE = re.compile(r"E2E_BUSY_HOLD(?::(\d+(?:\.\d+)?))?")
_PLAN_URL_RE = re.compile(r"https?://[^\s\"'<>)\]|]+/plan\b")
_ATTRIBUTION_RE = re.compile(r"@([A-Za-z0-9-]+):")
_THREAD_TOOLS_RE = re.compile(
    r"E2E_THREAD_TOOLS:([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
_THREAD_TOOLS_TARGET_TITLE = "E2E Thread Tools Target"
DELEGATE_MARKER = "E2E_DELEGATE"
SUBAGENT_TASK_MARKER = "E2E_SUBAGENT_TASK"
SLACK_REPLY_ORDER_MARKER = "E2E_SLACK_REPLY_ORDER"
SLACK_REPLY_GROUPED_ORDER_MARKER = "E2E_SLACK_REPLY_GROUPED_ORDER"

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
    message = (
        step.factory(messages)
        if step.factory is not None
        else AIMessage(
            content=step.content,
            tool_calls=[
                {"name": call.name, "args": dict(call.args), "id": call.call_id}
                for call in step.tool_calls
            ],
        )
    )
    for call in message.tool_calls:
        if call["name"] == "slack_read_thread_messages":
            call["args"].update(
                {"channel_id": DEMO_CHANNEL, "message_ts": _slack_thread_ts(messages)}
            )
    return message


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)


_FRAMING_SENDER_IDS = ("system:slack-context", "system:dashboard-handoff")
_METADATA_SENDER_IDS = tuple(sorted(TURN_ANNOTATION_SENDER_IDS))


def _is_framing_block(header: str) -> bool:
    """A system envelope that describes where the next turn came from."""
    return 'kind="system"' in header and any(
        f'sender="{sender}"' in header for sender in _FRAMING_SENDER_IDS
    )


def _is_metadata_block(header: str) -> bool:
    """A system envelope that annotates the turn instead of being one."""
    return 'kind="system"' in header and any(
        f'sender="{sender}"' in header for sender in _METADATA_SENDER_IDS
    )


def _script_humans(messages: list[BaseMessage]) -> list[HumanMessage]:
    """The user turns a script routes on, recovered from the structured stream.

    A Slack dispatch replays the thread's earlier messages as context before the
    system block that frames the mention, so only the message after that block is
    the turn. An instruction Open SWE dispatches to itself — a plan decision, a
    scheduled wake-up — carries no Slack timestamp and is always a turn, even
    though it too arrives as a system envelope.
    """
    selected: list[HumanMessage] = []
    slack_request_pending = False
    for message in messages:
        if not isinstance(message, HumanMessage):
            continue
        text = _text(message.content).lstrip()
        if text.startswith("<dynamic-context "):
            continue
        if text.startswith("<input-message "):
            header = text.split(">", 1)[0]
            if _is_metadata_block(header):
                continue
            if _is_framing_block(header):
                slack_request_pending = 'surface="slack"' in header
                continue
            if 'surface="slack"' in header and "<timestamp>" in text:
                if slack_request_pending:
                    selected.append(message)
                    slack_request_pending = False
                continue
        selected.append(message)
    return selected


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


def _expedite_watch_step(messages: list[BaseMessage]) -> AIMessage:
    """Ask for the durable CI watch on the PR the previous step opened."""
    url = _pr_url_from_messages(messages) or ""
    return AIMessage(
        content="Watching the pull request until CI settles.",
        tool_calls=[
            {
                "name": "manage_baby_sit",
                "args": {"pr_url": url, "action": "start"},
                "id": "call-expedite-watch",
            }
        ],
        response_metadata={"model_name": "fake-scripted-model"},
    )


def _expedite_request_step(messages: list[BaseMessage]) -> AIMessage:
    """Nominate the PR for expedited review in the Slack thread."""
    url = _pr_url_from_messages(messages) or ""
    return AIMessage(
        content="Asking for an expedited review in the thread.",
        tool_calls=[
            {
                "name": "expedite_pr_approval",
                "args": {"pr_url": url},
                "id": f"call-expedite-{len(messages)}",
            }
        ],
        response_metadata={"model_name": "fake-scripted-model"},
    )


def _expedite_opened_reply_step(messages: list[BaseMessage]) -> AIMessage:
    url = _pr_url_from_messages(messages) or "(PR url unavailable)"
    text = (
        f"Opened <{url}|{EXPEDITE_PR_TITLE}> and I'm watching its checks. "
        "I'll ask for an expedited review once they are green."
    )
    return AIMessage(
        content="Reporting the pull request in the Slack thread.",
        tool_calls=[
            {
                "name": "slack_thread_reply",
                "args": {"message": text},
                "id": f"call-expedite-opened-{len(messages)}",
            }
        ],
        response_metadata={"model_name": "fake-scripted-model"},
    )


def _expedite_reply_step(messages: list[BaseMessage]) -> AIMessage:
    url = _pr_url_from_messages(messages) or "(PR url unavailable)"
    text = (
        f"Asked for an expedited review of <{url}|{EXPEDITE_PR_TITLE}>. "
        "Two approvals in this thread will merge it."
    )
    return AIMessage(
        content="Replying in the Slack thread.",
        tool_calls=[
            {
                "name": "slack_thread_reply",
                "args": {"message": text},
                "id": f"call-expedite-reply-{len(messages)}",
            }
        ],
        response_metadata={"model_name": "fake-scripted-model"},
    )


def _expedite_fixed_reply_step(messages: list[BaseMessage]) -> AIMessage:
    url = _pr_url_from_messages(messages) or "(PR url unavailable)"
    text = (
        f"Fixed the failing check on <{url}|{EXPEDITE_PR_TITLE}> and pushed. "
        "Waiting for CI to go green before asking for approvals again."
    )
    return AIMessage(
        content="Reporting the fix in the Slack thread.",
        tool_calls=[
            {
                "name": "slack_thread_reply",
                "args": {"message": text},
                "id": f"call-expedite-fix-reply-{len(messages)}",
            }
        ],
        response_metadata={"model_name": "fake-scripted-model"},
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


PLAN_FILE_PATH = "/workspace/plans/2026-06-29-greet-helper.html"

PLAN_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Greeting Blueprint</title>
    <style>
      :root {
        color-scheme: light dark;
        --bg: #ffffff;
        --fg: #1c1c1c;
        --muted: #5c5c5c;
      }
      @media (prefers-color-scheme: dark) {
        :root:not([data-theme="light"]) {
          --bg: #1c1c1c;
          --fg: #f4f4f4;
          --muted: #a8a8a8;
        }
      }
      :root[data-theme="dark"] {
        --bg: #1c1c1c;
        --fg: #f4f4f4;
        --muted: #a8a8a8;
      }
      body {
        margin: 0;
        padding: 2rem 1.5rem;
        background: var(--bg);
        color: var(--fg);
        font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
        line-height: 1.6;
      }
      main { margin: 0 auto; max-width: 44rem; }
      h1 { font-size: 1.5rem; margin: 0 0 0.5rem; }
      h2 { font-size: 1.05rem; margin: 1.75rem 0 0.5rem; }
      p.lede { color: var(--muted); margin: 0; }
      code {
        background: rgba(127, 127, 127, 0.18);
        border-radius: 0.25rem;
        padding: 0.1em 0.35em;
      }
      a:focus-visible, :focus-visible { outline: 2px solid currentColor; outline-offset: 2px; }
    </style>
  </head>
  <body>
    <main>
      <h1>Add greet() helper</h1>
      <p class="lede">Add a tiny greeting helper to the demo repo.</p>

      <h2>Files to change</h2>
      <ul>
        <li><code>greet.py</code> — new module exposing a <code>greet(name)</code> function.</li>
      </ul>

      <h2>Steps</h2>
      <ol>
        <li>Create <code>greet.py</code> with a <code>greet(name)</code> function.</li>
        <li>Open a draft PR with the change.</li>
      </ol>

      <h2>Verification</h2>
      <ul>
        <li>Import <code>greet</code> and confirm it returns the expected string.</li>
      </ul>
    </main>
  </body>
</html>
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
                "args": {"file_path": PLAN_FILE_PATH, "content": PLAN_HTML},
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


WORKSPACE_NAME = "default"
WORKSPACE_PROMPT = (
    "Checkouts live in /workspace/repos. Build with `make build`, test with `make test`."
)
WORKSPACE_SETUP_SCRIPT = (
    "set -euo pipefail\nmkdir -p repos && echo provisioned > repos/.provisioned && ls -a repos"
)
WORKSPACE_UPDATE_SCRIPT = "echo refreshed >> repos/.provisioned"

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
        time.sleep(0.5)
    attribution = _latest_attribution(messages)
    suffix = f" I saw this follow-up was from {attribution}." if attribution else ""
    return AIMessage(content=f"{FOLLOW_UP_REPLY}{suffix}")


def _tool_payload(messages: list[BaseMessage], tool_name: str) -> dict[str, Any]:
    for message in reversed(messages):
        if not isinstance(message, ToolMessage) or message.name != tool_name:
            continue
        payload = (
            json.loads(message.content) if isinstance(message.content, str) else message.content
        )
        if isinstance(payload, dict):
            return payload
    raise ValueError(f"{tool_name} result is missing")


def _listed_thread_id(messages: list[BaseMessage]) -> str:
    items = _tool_payload(messages, "list_threads").get("items")
    matches = [
        item.get("id")
        for item in items or []
        if isinstance(item, dict) and item.get("title") == _THREAD_TOOLS_TARGET_TITLE
    ]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise ValueError("list_threads did not return exactly one target thread")
    return matches[0]


def _inspected_thread_id(messages: list[BaseMessage]) -> str:
    thread = _tool_payload(messages, "get_thread").get("thread")
    thread_id = thread.get("id") if isinstance(thread, dict) else None
    if not isinstance(thread_id, str):
        raise ValueError("get_thread did not return the target thread")
    return thread_id


def _workspace_poll_step(messages: list[BaseMessage]) -> AIMessage:
    """Follow the reproducibility rebuild through the one poll tool."""
    task_id = _tool_payload(messages, "refresh_workspace_start").get("task_id")
    if not isinstance(task_id, str):
        raise ValueError("refresh_workspace_start did not return a task id")
    return AIMessage(
        content="Following the rebuild.",
        tool_calls=[
            {
                "name": "background_task",
                "args": {"action": "status", "task_id": task_id},
                "id": "call-env-poll",
            }
        ],
    )


def _list_threads_step(_messages: list[BaseMessage]) -> AIMessage:
    return AIMessage(
        content="Finding the target thread.",
        tool_calls=[
            {
                "name": "list_threads",
                "args": {"query": _THREAD_TOOLS_TARGET_TITLE, "resolved": False},
                "id": "call-list-threads",
            }
        ],
    )


def _get_thread_step(messages: list[BaseMessage]) -> AIMessage:
    return AIMessage(
        content="Inspecting the target thread.",
        tool_calls=[
            {
                "name": "get_thread",
                "args": {"thread_id": _listed_thread_id(messages)},
                "id": "call-get-thread",
            }
        ],
    )


def _resolve_thread_step(messages: list[BaseMessage]) -> AIMessage:
    return AIMessage(
        content="Resolving the target thread.",
        tool_calls=[
            {
                "name": "manage_thread",
                "args": {"thread_id": _inspected_thread_id(messages), "action": "resolve"},
                "id": "call-manage-thread",
            }
        ],
    )


SCRIPT_LIBRARY: dict[str, tuple[StepSpec, ...]] = {
    # Parent turn: delegate to two general-purpose subagents in one step so the
    # transcript renders a subagent card grid. The subagents run this same fake
    # model; their task description carries the marker that selects the
    # ``subagent_task`` script below.
    "delegate": (
        StepSpec(
            content="Delegating the investigation to two subagents.",
            tool_calls=(
                _tool_call(
                    "task",
                    {
                        "description": f"{SUBAGENT_TASK_MARKER} inspect the repository layout",
                        "subagent_type": "general-purpose",
                    },
                    "call-subagent-layout",
                ),
                _tool_call(
                    "task",
                    {
                        "description": f"{SUBAGENT_TASK_MARKER} list the top-level files",
                        "subagent_type": "general-purpose",
                    },
                    "call-subagent-files",
                ),
            ),
        ),
        StepSpec(content="Both subagents finished their investigation."),
    ),
    # Subagent turn: a briefly slow shell step first so a spec that opens the
    # thread right after the run starts can watch the nested activity live.
    "subagent_task": (
        _tool_step(
            "Looking around the workspace.",
            "execute",
            {"command": "sleep 3 && ls"},
            "call-subagent-ls",
        ),
        _tool_step(
            "Confirming the listing.",
            "execute",
            {"command": "echo subagent-done"},
            "call-subagent-echo",
        ),
        StepSpec(content="Subagent finished: listed the workspace."),
    ),
    "thread_tools": (
        _dynamic_step(_list_threads_step),
        _dynamic_step(_get_thread_step),
        _dynamic_step(_resolve_thread_step),
        StepSpec(content="Resolved the target thread through the thread tools."),
    ),
    "code_channel": (
        _tool_step(
            "This task warrants a dedicated Slack code channel.",
            "manage_code_channel",
            {"action": "create", "title": "Investigate flaky CI failures"},
            "call-code-channel-create",
        ),
        _tool_step(
            "Continuing the task in its dedicated code channel.",
            "slack_thread_reply",
            {
                "message": "I created this code channel for the investigation. All updates and follow-ups stay in this one Open SWE session."
            },
            "call-code-channel-reply",
        ),
        _tool_step(
            "Marking the code-channel session active.",
            "manage_code_channel",
            {"action": "status", "status": "active"},
            "call-code-channel-active",
        ),
    ),
    "code_channel_followup": (
        _tool_step(
            "Replying to the unmentioned code-channel follow-up.",
            "slack_thread_reply",
            {
                "message": "Status: the investigation is active, and this unmentioned follow-up reached the same Open SWE session."
            },
            "call-code-channel-followup",
        ),
    ),
    "iframe": (
        _tool_step(
            "Acknowledging the iframe preview request.",
            "slack_thread_reply",
            {"message": "Preparing the iframe preview now."},
            "call-iframe-ack",
        ),
        _tool_step(
            "Writing the iframe HTML.",
            "write_file",
            {"file_path": _IFRAME_HTML_PATH, "content": _IFRAME_HTML},
            "call-iframe-html",
        ),
        _tool_step(
            "Rendering the iframe preview.",
            "output_iframe",
            {
                "path": _IFRAME_HTML_PATH,
                "title": "Iframe E2E Preview",
            },
            "call-output-iframe",
        ),
        StepSpec(content="Rendered the iframe preview."),
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
    "slack_reply_order": (
        _tool_step(
            "Acknowledging the Slack request before starting work.",
            "slack_thread_reply",
            {"message": "On it!"},
            "call-order-ack",
        ),
        _tool_step(
            "Running a long task while another Slack message arrives.",
            "execute",
            {"command": "sleep 20"},
            "call-order-sleep",
        ),
        StepSpec(content="Finished the long task."),
    ),
    "slack_reply_grouped_order": (
        _tool_step(
            "Acknowledging the Slack request before delegating work.",
            "slack_thread_reply",
            {"message": "On it!"},
            "call-grouped-order-ack",
        ),
        _tool_step(
            "Delegating a long-running investigation.",
            "task",
            {
                "description": (
                    f"{SUBAGENT_TASK_MARKER} E2E_BUSY_HOLD:10 inspect the grouped ordering"
                ),
                "subagent_type": "general-purpose",
            },
            "call-grouped-order-task",
        ),
        StepSpec(content="Finished the delegated investigation."),
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
    # Expedited review, turn 1: implement a one-line change, open a ready PR,
    # start the durable CI watch, and nominate it for approval in Slack. The
    # card cannot appear yet — checks have not reported.
    "expedite": (
        _tool_step(
            "Acknowledging the request.",
            "slack_thread_reply",
            {"message": "On it — this is a one-liner, I'll ask for an expedited review."},
            "call-expedite-ack",
        ),
        _tool_step(
            "Making the change and pushing the branch.",
            "execute",
            {"command": _EXPEDITE_SETUP_SCRIPT},
            "call-expedite-setup",
        ),
        _tool_step(
            "Opening a pull request that is ready for review.",
            "open_pull_request",
            {
                "owner": OWNER,
                "repo": REPO,
                "head": FEATURE_BRANCH,
                "base": BASE_BRANCH,
                "title": EXPEDITE_PR_TITLE,
                "body": "Restores the single exclamation mark in the greeting.",
                "draft": False,
                "resolves_thread": True,
            },
            "call-expedite-pr",
        ),
        _dynamic_step(_expedite_watch_step),
        _dynamic_step(_expedite_opened_reply_step),
    ),
    # Turn 2: a failing check woke the watch. Fix the code and push; the next
    # green webhook is what lets the pending approval post its card.
    "expedite_fix": (
        _tool_step(
            "Fixing the failing check and pushing.",
            "execute",
            {"command": _EXPEDITE_FIX_SCRIPT},
            "call-expedite-fix",
        ),
        # Ask in the same turn. The check is still red, so the request has to
        # park until CI reports green rather than posting a card now.
        _dynamic_step(_expedite_request_step),
        _dynamic_step(_expedite_fixed_reply_step),
    ),
    # Turn 3: checks are green (or an earlier round was withdrawn). Ask for the
    # expedited review now that the pull request is clean.
    "expedite_retry": (
        _dynamic_step(_expedite_request_step),
        _dynamic_step(_expedite_reply_step),
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
    "many_files": (
        _tool_step(
            "Acknowledging the Slack request before starting work.",
            "slack_thread_reply",
            {"message": "On it!"},
            "call-ack",
        ),
        _tool_step(
            "Setting up the repo and implementing the change.",
            "execute",
            {"command": _MANY_FILES_IMPLEMENT_SCRIPT},
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
                "body": "Adds multiple files for changed-file coverage.",
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
    "move": (
        _tool_step(
            "Moving the Slack thread to its destination channel.",
            "slack_move_thread",
            {
                "message": "Continue the existing Open SWE task in this channel.",
                "channel_id": "C_TARGET",
            },
            "call-move",
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
    "workspace": (
        # Build here, with ordinary tools, then publish this sandbox as the image.
        _tool_step(
            "Provisioning this sandbox.",
            "execute",
            {"command": WORKSPACE_SETUP_SCRIPT},
            "call-env-provision",
        ),
        _tool_step(
            "Publishing this sandbox as the workspace image.",
            "publish_workspace",
            {
                "name": WORKSPACE_NAME,
                "prompt": WORKSPACE_PROMPT,
                "setup_script": WORKSPACE_SETUP_SCRIPT,
                "update_script": WORKSPACE_UPDATE_SCRIPT,
                "repos": [f"{OWNER}/{REPO}"],
            },
            "call-env-publish",
        ),
        # Then prove the script reproduces it, the way the nightly cron will.
        _tool_step(
            "Checking the setup script reproduces the image.",
            "refresh_workspace_start",
            {"name": WORKSPACE_NAME},
            "call-env-refresh",
        ),
        _dynamic_step(_workspace_poll_step),
        StepSpec(content=f"The `{WORKSPACE_NAME}` workspace is captured and live."),
    ),
    "followup": (_dynamic_step(_followup_step),),
}


def _is_thread_tools_request(text: str) -> bool:
    return _THREAD_TOOLS_RE.search(text) is not None


def _is_iframe_request(text: str) -> bool:
    return "E2E_IFRAME" in text


def _is_plan_request(text: str) -> bool:
    return "plan" in text.lower()


def _is_workspace_request(text: str) -> bool:
    lowered = text.lower()
    return "workspace" in lowered or "environment" in lowered


def _is_breakout_request(text: str) -> bool:
    t = text.lower()
    return "break out" in t or "separate thread" in t or "split out" in t


def _is_move_request(text: str) -> bool:
    return "E2E_MOVE_THREAD" in text


def _is_move_followup(text: str) -> bool:
    return "E2E_DESTINATION_FOLLOWUP" in text or "E2E_SOURCE_RETAG" in text


def _is_pull_request_fix(text: str) -> bool:
    """A dashboard PR-fix dispatch, whose prompt names an existing PR to repair.

    Without this it lands on the catch-all implement script and opens a *new* PR,
    which renumbers the fake store under whichever spec runs next."""
    return "Fix merge conflicts and failing CI checks on" in text


def _is_approval(text: str) -> bool:
    t = text.lower()
    return "the plan has been approved" in t or (
        ("approve" in t or "approved" in t) and "implement" in t
    )


def _is_revision(text: str) -> bool:
    t = text.lower()
    return "needs changes" in t or "publish an updated plan" in t


SCRIPT_RULES: tuple[ScriptRule, ...] = (
    ScriptRule(
        "subagent_task",
        lambda ctx: ctx.human_count <= 1 and SUBAGENT_TASK_MARKER in ctx.first_text,
    ),
    ScriptRule("delegate", lambda ctx: ctx.human_count <= 1 and DELEGATE_MARKER in ctx.first_text),
    ScriptRule(
        "slack_reply_grouped_order",
        lambda ctx: SLACK_REPLY_GROUPED_ORDER_MARKER in ctx.first_text,
    ),
    ScriptRule(
        "slack_reply_order",
        lambda ctx: SLACK_REPLY_ORDER_MARKER in ctx.first_text,
    ),
    ScriptRule(
        "thread_tools",
        lambda ctx: ctx.human_count <= 1 and _is_thread_tools_request(ctx.first_text),
    ),
    ScriptRule(
        "code_channel_followup",
        lambda ctx: "E2E_CODE_CHANNEL_FOLLOWUP" in ctx.last_text,
    ),
    ScriptRule(
        "code_channel",
        lambda ctx: ctx.human_count <= 1 and "E2E_CODE_CHANNEL" in ctx.first_text,
    ),
    ScriptRule("iframe", lambda ctx: ctx.human_count <= 1 and _is_iframe_request(ctx.first_text)),
    ScriptRule(
        "desktop",
        lambda ctx: ctx.human_count <= 1 and "E2E_DESKTOP_LOCAL" in ctx.first_text,
    ),
    ScriptRule(
        "workspace", lambda ctx: ctx.human_count <= 1 and _is_workspace_request(ctx.first_text)
    ),
    ScriptRule(
        "expedite_fix",
        lambda ctx: EXPEDITE_MARKER in ctx.first_text and "/baby-sit --continue" in ctx.last_text,
    ),
    ScriptRule(
        "expedite_retry",
        lambda ctx: (
            EXPEDITE_MARKER in ctx.first_text
            and (
                EXPEDITE_NOW_MARKER in ctx.last_text
                or "was withdrawn before it could merge" in ctx.last_text
            )
        ),
    ),
    ScriptRule("expedite", lambda ctx: EXPEDITE_MARKER in ctx.first_text),
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
    ScriptRule(
        "many_files", lambda ctx: ctx.human_count <= 1 and "E2E_MANY_FILES" in ctx.first_text
    ),
    ScriptRule("move", lambda ctx: ctx.human_count <= 1 and _is_move_request(ctx.first_text)),
    ScriptRule("followup", lambda ctx: _is_pull_request_fix(ctx.first_text)),
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

    model: str = "fake"
    profile: ModelProfile | None = {
        "tool_calling": True,
        "max_input_tokens": 128_000,
    }
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
        for message in messages:
            if isinstance(message, SystemMessage):
                LAST_SYSTEM_PROMPT["text"] = _text(message.content)
                break
        humans = _script_humans(messages)
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
