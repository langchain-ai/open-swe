from openai import OpenAI
from pathlib import Path
import argparse
import json
import re
import subprocess
import sys
import time

MODEL = "qwen2.5-coder:14b"
DEFAULT_MAX_STEPS = 40
DEFAULT_MAX_FILE_CHARS = 20000

client = OpenAI(
    base_url="http://127.0.0.1:11434/v1",
    api_key="ollama",
)

IGNORE_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
    "node_modules", ".mypy_cache", ".ruff_cache", "dist", "build"
}

ALLOWED_COMMAND_PREFIXES = [
    "pytest",
    "python -m pytest",
    "ls",
    "cat",
    "grep",
    "ruff",
    "flake8",
]


def run_subprocess(command, cwd: Path, shell: bool = False) -> tuple[int, str]:
    proc = subprocess.run(
        command,
        cwd=cwd,
        shell=shell,
        capture_output=True,
        text=True,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.strip()


def safe_repo_path(repo: Path, rel_path: str) -> Path:
    target = (repo / rel_path).resolve()
    try:
        target.relative_to(repo)
    except ValueError:
        raise RuntimeError(f"Refusing outside repo: {target}")
    return target


def is_git_repo(repo: Path) -> bool:
    code, _ = run_subprocess(["git", "rev-parse", "--is-inside-work-tree"], repo)
    return code == 0


def current_git_branch(repo: Path) -> str:
    code, output = run_subprocess(["git", "branch", "--show-current"], repo)
    return output.strip() if code == 0 else ""


def backup_file(target: Path) -> None:
    backup = target.with_suffix(target.suffix + ".bak")
    if target.exists() and not backup.exists():
        backup.write_text(target.read_text())


def tool_list_files(repo: Path) -> str:
    files = []
    for path in repo.rglob("*"):
        rel = path.relative_to(repo)
        if any(part in IGNORE_DIRS for part in rel.parts):
            continue
        if path.is_file():
            files.append(str(rel))
    files.sort()
    return json.dumps({"ok": True, "files": files}, indent=2)


def tool_read_file(repo: Path, path: str, max_chars: int) -> str:
    target = safe_repo_path(repo, path)
    if not target.exists():
        return json.dumps({"ok": False, "error": f"File not found: {path}"}, indent=2)
    if not target.is_file():
        return json.dumps({"ok": False, "error": f"Not a file: {path}"}, indent=2)

    text = target.read_text()
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True

    return json.dumps(
        {
            "ok": True,
            "path": path,
            "truncated": truncated,
            "content": text,
        },
        indent=2,
    )


def tool_write_file(repo: Path, path: str, content: str) -> str:
    target = safe_repo_path(repo, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup_file(target)
    target.write_text(content.rstrip() + "\n")
    return json.dumps(
        {
            "ok": True,
            "path": path,
            "bytes_written": len(content.encode("utf-8")),
            "mode": "full_write",
        },
        indent=2,
    )


def tool_replace_in_file(repo: Path, path: str, old: str, new: str, count: int = 1) -> str:
    target = safe_repo_path(repo, path)
    if not target.exists():
        return json.dumps({"ok": False, "error": f"File not found: {path}"}, indent=2)

    text = target.read_text()
    occurrences = text.count(old)
    if occurrences == 0:
        return json.dumps(
            {
                "ok": False,
                "error": "Old snippet not found in file.",
                "path": path,
            },
            indent=2,
        )

    backup_file(target)
    updated = text.replace(old, new, count)
    target.write_text(updated)

    return json.dumps(
        {
            "ok": True,
            "path": path,
            "mode": "replace_in_file",
            "occurrences_before": occurrences,
            "replaced_count": min(count, occurrences),
        },
        indent=2,
    )


def tool_append_to_file(repo: Path, path: str, content: str) -> str:
    target = safe_repo_path(repo, path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        backup_file(target)
        existing = target.read_text()
    else:
        existing = ""

    if existing and not existing.endswith("\n"):
        existing += "\n"

    target.write_text(existing + content)

    return json.dumps(
        {
            "ok": True,
            "path": path,
            "mode": "append_to_file",
            "bytes_appended": len(content.encode("utf-8")),
        },
        indent=2,
    )


def is_command_allowed(command: str) -> bool:
    return any(command.startswith(prefix) for prefix in ALLOWED_COMMAND_PREFIXES)


def tool_run_shell(repo: Path, command: str) -> str:
    if not is_command_allowed(command):
        return json.dumps(
            {
                "ok": False,
                "error": f"Command not allowed: {command}",
                "allowed_prefixes": ALLOWED_COMMAND_PREFIXES,
            },
            indent=2,
        )

    code, output = run_subprocess(command, repo, shell=True)
    return json.dumps(
        {
            "ok": code == 0,
            "returncode": code,
            "command": command,
            "output": output,
        },
        indent=2,
    )


def tool_git_status(repo: Path) -> str:
    if not is_git_repo(repo):
        return json.dumps({"ok": False, "error": "Not a git repository."}, indent=2)

    code, output = run_subprocess(["git", "status", "--short"], repo)
    return json.dumps({"ok": code == 0, "output": output}, indent=2)


def tool_git_diff(repo: Path, path: str | None = None) -> str:
    if not is_git_repo(repo):
        return json.dumps({"ok": False, "error": "Not a git repository."}, indent=2)

    if path:
        safe_repo_path(repo, path)
        cmd = ["git", "diff", "--", path]
    else:
        cmd = ["git", "diff"]

    code, output = run_subprocess(cmd, repo)
    return json.dumps({"ok": code == 0, "path": path, "output": output}, indent=2)


def tool_git_commit(repo: Path, message: str) -> str:
    if not is_git_repo(repo):
        return json.dumps({"ok": False, "error": "Not a git repository."}, indent=2)

    code1, out1 = run_subprocess(["git", "add", "-A"], repo)
    if code1 != 0:
        return json.dumps({"ok": False, "error": out1}, indent=2)

    code2, out2 = run_subprocess(["git", "commit", "-m", message], repo)
    return json.dumps({"ok": code2 == 0, "output": out2}, indent=2)


def tool_git_restore_file(repo: Path, path: str) -> str:
    if not is_git_repo(repo):
        return json.dumps({"ok": False, "error": "Not a git repository."}, indent=2)

    safe_repo_path(repo, path)
    code, output = run_subprocess(["git", "restore", "--", path], repo)
    return json.dumps({"ok": code == 0, "path": path, "output": output}, indent=2)


def tool_git_restore_all(repo: Path) -> str:
    if not is_git_repo(repo):
        return json.dumps({"ok": False, "error": "Not a git repository."}, indent=2)

    code, output = run_subprocess(["git", "restore", "."], repo)
    return json.dumps({"ok": code == 0, "output": output}, indent=2)


def tool_git_new_branch(repo: Path, name: str) -> str:
    if not is_git_repo(repo):
        return json.dumps({"ok": False, "error": "Not a git repository."}, indent=2)

    code, output = run_subprocess(["git", "checkout", "-b", name], repo)
    return json.dumps({"ok": code == 0, "branch": name, "output": output}, indent=2)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in the repository.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the repository.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write the full replacement contents of a file in the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "Replace an exact snippet in a file with a new snippet. Prefer this over full file rewrites when possible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                    "count": {"type": "integer"},
                },
                "required": ["path", "old", "new"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_to_file",
            "description": "Append content to the end of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a safe shell command inside the repository.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show git status for the repository.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show git diff for the whole repo or a specific file.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Commit current changes with a message after tests pass.",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_restore_file",
            "description": "Restore one tracked file to its git state.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_restore_all",
            "description": "Restore all tracked files to their git state.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_new_branch",
            "description": "Create and switch to a new branch.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
]


def call_model(messages, tools=None, tool_choice="auto", max_tokens=1400):
    kwargs = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    if tools is not None:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice
    return client.chat.completions.create(**kwargs)


def handle_tool(repo: Path, max_file_chars: int, tool_name: str, tool_args_json: str) -> str:
    try:
        args = json.loads(tool_args_json) if tool_args_json else {}
    except json.JSONDecodeError:
        return json.dumps({"ok": False, "error": f"Invalid JSON args: {tool_args_json}"}, indent=2)

    try:
        if tool_name == "list_files":
            return tool_list_files(repo)
        if tool_name == "read_file":
            return tool_read_file(repo, args["path"], max_file_chars)
        if tool_name == "write_file":
            return tool_write_file(repo, args["path"], args["content"])
        if tool_name == "replace_in_file":
            return tool_replace_in_file(repo, args["path"], args["old"], args["new"], args.get("count", 1))
        if tool_name == "append_to_file":
            return tool_append_to_file(repo, args["path"], args["content"])
        if tool_name == "run_shell":
            return tool_run_shell(repo, args["command"])
        if tool_name == "git_status":
            return tool_git_status(repo)
        if tool_name == "git_diff":
            return tool_git_diff(repo, args.get("path"))
        if tool_name == "git_commit":
            return tool_git_commit(repo, args["message"])
        if tool_name == "git_restore_file":
            return tool_git_restore_file(repo, args["path"])
        if tool_name == "git_restore_all":
            return tool_git_restore_all(repo)
        if tool_name == "git_new_branch":
            return tool_git_new_branch(repo, args["name"])
        return json.dumps({"ok": False, "error": f"Unknown tool: {tool_name}"}, indent=2)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, indent=2)


def extract_json_block(text: str):
    text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    for block in fenced:
        try:
            return json.loads(block)
        except Exception:
            pass

    inline = re.findall(r"(\{.*\})", text, re.S)
    for block in inline:
        try:
            return json.loads(block)
        except Exception:
            pass

    return None


def extract_pseudo_tool_call(text: str):
    data = extract_json_block(text)
    if not isinstance(data, dict):
        return None

    function_name = data.get("function") or data.get("name")
    if not isinstance(function_name, str):
        return None

    if "arguments" in data and isinstance(data["arguments"], dict):
        return function_name, json.dumps(data["arguments"])

    args = data.get("args")
    if isinstance(args, list):
        if function_name in {"read_file", "git_diff", "git_restore_file"} and len(args) >= 1:
            return function_name, json.dumps({"path": args[0]})
        if function_name == "git_new_branch" and len(args) >= 1:
            return function_name, json.dumps({"name": args[0]})
        if function_name == "git_commit" and len(args) >= 1:
            return function_name, json.dumps({"message": args[0]})
    if isinstance(args, dict):
        return function_name, json.dumps(args)

    return None


def get_critique(history_summary: str, latest_test_output: str) -> str:
    critique_messages = [
        {
            "role": "system",
            "content": (
                "You are a strict reviewer of a coding agent. "
                "Given prior attempts and the latest failing test output, "
                "identify what likely went wrong and what the next attempt should do differently. "
                "Prefer small targeted changes and using diff inspection before more edits."
            ),
        },
        {
            "role": "user",
            "content": (
                "Prior attempt summary:\n"
                f"{history_summary}\n\n"
                "Latest failing test output:\n"
                f"{latest_test_output}\n\n"
                "Return a short critique and next-step advice."
            ),
        },
    ]
    resp = call_model(critique_messages, tools=None, max_tokens=300)
    return (resp.choices[0].message.content or "").strip()


def ensure_branch_per_run(repo: Path) -> str:
    if not is_git_repo(repo):
        return ""

    current = current_git_branch(repo)
    branch_name = f"agent-run-{int(time.time())}"

    code, output = run_subprocess(["git", "checkout", "-b", branch_name], repo)
    if code == 0:
        print(f"Created branch: {branch_name}")
        return branch_name

    print(f"Branch creation skipped: {output}")
    if current:
        print(f"Continuing on existing branch: {current}")
        return current

    return ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="Path to repo")
    parser.add_argument("--test-cmd", default="pytest -q", help="Test command")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    parser.add_argument("--max-file-chars", type=int, default=DEFAULT_MAX_FILE_CHARS)
    args = parser.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    if not repo.exists():
        print(f"Missing repo path: {repo}", file=sys.stderr)
        raise SystemExit(1)

    branch_name = ensure_branch_per_run(repo)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a careful Python coding agent working in a local repository.\n"
                "You MUST use tools for actions.\n"
                "Do not merely describe tool calls in text.\n"
                "If you want to inspect a diff, call git_diff.\n"
                "If you want to run tests, call run_shell.\n"
                "Use tools to inspect files, run tests, inspect diffs, and patch code.\n"
                "Prefer replace_in_file for small targeted edits.\n"
                "Use git_status and git_diff to inspect your changes.\n"
                "After tests pass, inspect git_diff and optionally commit with a concise message.\n"
                "Do not repeat failed ideas.\n"
                "When tests pass, respond with a short summary."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Repository: {repo}\n"
                f"Current branch: {branch_name or '(unknown or no git branch)'}\n"
                f"Goal: make this pass: {args.test_cmd}\n\n"
                "Suggested workflow:\n"
                "1. Run tests.\n"
                "2. Read relevant files.\n"
                "3. Make the smallest correct patch.\n"
                "4. Re-run tests.\n"
                "5. Inspect git diff.\n"
                "6. If tests pass, commit the fix.\n"
            ),
        },
    ]

    attempt_notes = []

    for step in range(1, args.max_steps + 1):
        print(f"\n=== AGENT STEP {step} ===")

        resp = call_model(messages, tools=TOOLS, tool_choice="auto", max_tokens=1400)
        msg = resp.choices[0].message

        if msg.tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": tc.type,
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )

            ran_tests = False
            latest_test_output = ""
            tests_passed = False

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                tool_args = tc.function.arguments or "{}"

                print(f"Tool call: {tool_name}({tool_args})")
                result = handle_tool(repo, args.max_file_chars, tool_name, tool_args)
                print("Tool result:")
                print(result)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )

                if tool_name == "run_shell":
                    try:
                        data = json.loads(result)
                    except json.JSONDecodeError:
                        data = {"ok": False, "output": result, "command": ""}

                    if data.get("command") == args.test_cmd:
                        ran_tests = True
                        latest_test_output = data.get("output", "")
                        tests_passed = data.get("ok") is True

            if ran_tests and tests_passed:
                final_resp = call_model(messages, tools=None, max_tokens=300)
                final_text = final_resp.choices[0].message.content or "Tests passed."
                print("\n=== FINAL RESPONSE ===")
                print(final_text.strip())
                return

            if ran_tests and latest_test_output:
                attempt_notes.append(f"Step {step} test output:\n{latest_test_output[:1500]}")
                critique = get_critique("\n\n".join(attempt_notes[-4:]), latest_test_output)
                print("\n=== CRITIQUE ===")
                print(critique)
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Reviewer critique for the latest failure:\n"
                            f"{critique}\n\n"
                            "Revise your plan. Inspect diffs before making larger changes."
                        ),
                    }
                )

            continue

        final_text = (msg.content or "").strip()
        if final_text:
            print("\n=== MODEL RESPONSE ===")
            print(final_text)

            pseudo = extract_pseudo_tool_call(final_text)
            if pseudo:
                tool_name, tool_args = pseudo
                print("\n=== SALVAGED TOOL CALL ===")
                print(f"{tool_name}({tool_args})")

                result = handle_tool(repo, args.max_file_chars, tool_name, tool_args)
                print("Tool result:")
                print(result)

                messages.append({"role": "assistant", "content": final_text})
                messages.append(
                    {
                        "role": "user",
                        "content": "Your previous message described a tool call in text. I executed it for you. Continue by using real tool calls from now on."
                    }
                )
                messages.append({"role": "tool", "tool_call_id": f"salvaged_{step}", "content": result})

                if tool_name == "run_shell":
                    try:
                        data = json.loads(result)
                    except json.JSONDecodeError:
                        data = {"ok": False, "output": result, "command": ""}

                    if data.get("command") == args.test_cmd and data.get("ok") is True:
                        print("\n=== FINAL RESPONSE ===")
                        print("Tests passed.")
                        return

                continue

            messages.append({"role": "assistant", "content": final_text})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"You must use actual tool calls, not described JSON in text. "
                        f"If you think it is fixed, run `{args.test_cmd}`. "
                        "If you want a diff, call git_diff as a real tool."
                    ),
                }
            )
        else:
            messages.append({"role": "assistant", "content": ""})

    print("\nFailed: reached max steps without confirmed passing tests.", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
