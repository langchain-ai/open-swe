"""PR Review evals — pytest-based, aligned with agent-builder eval pattern."""

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from anthropic import Anthropic
from dotenv import load_dotenv
from langgraph_sdk import get_client
from langsmith import testing as t

load_dotenv(Path(__file__).parent.parent.parent / ".env")

LANGGRAPH_URL = os.environ.get("LANGGRAPH_URL") or os.environ.get("LANGGRAPH_URL_PROD")
GITHUB_PAT = os.environ.get("GITHUB_PAT", "")
DATASET_PATH = Path(__file__).parent / "dataset.json"
DIFFS_DIR = Path(__file__).parent / "diffs"
RESULTS_PATH = Path(__file__).parent / "eval_results.json"

DIFFS_DIR.mkdir(exist_ok=True)

pytestmark = pytest.mark.langsmith


# ---------------------------------------------------------------------------
# Dataset fixture
# ---------------------------------------------------------------------------


def load_dataset() -> list[dict[str, Any]]:
    return json.loads(DATASET_PATH.read_text())


def pytest_generate_tests(metafunc):
    if "eval_entry" in metafunc.fixturenames:
        dataset = load_dataset()
        metafunc.parametrize("eval_entry", dataset, ids=[e["id"] for e in dataset])


# ---------------------------------------------------------------------------
# Prompt — matches process_github_pr_ready_for_review in webapp.py exactly
# ---------------------------------------------------------------------------

_SKILL_PATH = Path(__file__).parent.parent.parent / "skills" / "pr-review" / "SKILL.md"


def _load_skill() -> str:
    try:
        return _SKILL_PATH.read_text()
    except FileNotFoundError:
        return ""


async def fetch_commit_diff(pr_url: str, commit_id: str) -> str:
    """Fetch the diff for an exact commit SHA, using local cache if available."""
    cache_file = DIFFS_DIR / f"{commit_id}.diff"
    if cache_file.exists():
        return cache_file.read_text()

    parts = pr_url.rstrip("/").split("/")
    owner, repo = parts[-4], parts[-3]
    url = f"https://api.github.com/repos/{owner}/{repo}/commits/{commit_id}"
    headers = {
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Accept": "application/vnd.github.v3.diff",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        response = await client.get(url, headers=headers)
    if response.status_code == 200:
        diff = response.text
        cache_file.write_text(diff)
        return diff
    return ""


def build_review_prompt(pr_url: str, pr_title: str, pr_body: str = "", commit_id: str = "") -> str:
    """Build review prompt matching process_github_pr_ready_for_review in webapp.py exactly."""
    parts = pr_url.rstrip("/").split("/")
    owner, repo = parts[-4], parts[-3]

    prompt = f"This PR has been marked ready for review.\n\nPR: {pr_url}\nTitle: {pr_title}\n"
    if pr_body:
        prompt += f"Description: {pr_body}\n"
    if commit_id:
        prompt += (
            f"\n**IMPORTANT: Review at commit `{commit_id}`.**\n"
            f"Before reviewing, you MUST fetch and checkout this exact commit. Run these commands in order:\n"
            f"```\n"
            f"cd /workspace/{repo}\n"
            f"git remote set-url origin https://x-access-token:{GITHUB_PAT}@github.com/{owner}/{repo}.git\n"
            f"git fetch origin {commit_id}\n"
            f"git checkout {commit_id}\n"
            f"```\n"
            f"Do NOT proceed with the review until `git checkout {commit_id}` succeeds.\n"
            f"Then review the diff with `git diff origin/main...HEAD`.\n"
        )
    prompt += (
        "\n\nPlease review this PR thoroughly.\n\n"
        "IMPORTANT RULES:\n"
        "- REVIEW ONLY — do NOT write, edit, or commit any code\n"
        "- Use `create_pr_review` to submit your review — this is the ONLY comment you should leave\n"
        "- Do NOT call `github_comment` separately — the review body is your summary\n"
        "- Keep feedback concise: flag only real issues, skip style nits\n"
        "- Inline comments should be short and actionable, not essays"
    )
    skill_content = _load_skill()
    if skill_content:
        prompt += f"\n\n---\n\n## PR Review Skill\n\n{skill_content}"
    return prompt


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------


async def run_agent_on_pr(entry: dict[str, Any]) -> str:
    """Run the agent on a PR and return its output as a string."""
    client = get_client(url=LANGGRAPH_URL)
    thread_id = str(uuid.uuid4())
    prompt = build_review_prompt(
        entry["pr_url"], entry["pr_title"], entry.get("pr_body", ""), entry.get("commit_id", "")
    )

    # Create thread with PR ref + commit_id so server.py checks out the exact commit
    pr_number = entry["pr_number"]
    commit_id = entry.get("commit_id", "")
    thread_metadata: dict[str, str] = {
        "branch_name": f"refs/pull/{pr_number}/head",
    }
    if commit_id:
        thread_metadata["commit_id"] = commit_id

    await client.threads.create(
        thread_id=thread_id,
        if_exists="do_nothing",
        metadata=thread_metadata,
    )

    configurable = {
        "source": "github",
        "github_login": "aran-yogesh",
        "github_user_id": 0,
        "repo": entry["input"]["configurable"]["repo"],
        "pr_number": pr_number,
        "review_mode": True,
        "mode": "eval",
        "linear_issue": {
            "linear_project_id": "",
            "linear_issue_number": "",
        },
    }

    agent_output = ""
    intercepted_review = ""
    stream = client.runs.stream(
        thread_id,
        "agent",
        input={"messages": [{"role": "user", "content": prompt}]},
        config={"configurable": configurable},
        if_not_exists="create",
        stream_mode="values",
    )
    async for chunk in stream:
        messages = chunk.data.get("messages", []) if isinstance(chunk.data, dict) else []
        if messages:
            last = messages[-1]
            role = last.get("type") or last.get("role", "")
            if role in ("ai", "assistant"):
                content = last.get("content", "")
                if isinstance(content, str) and content:
                    agent_output = content
                elif isinstance(content, list):
                    # extract text blocks from list content (e.g. [{"type": "text", "text": "..."}])
                    text = " ".join(
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                    if text:
                        agent_output = text
            elif role == "tool":
                # capture intercepted review/comment tool results
                content = last.get("content", "")
                try:
                    payload = json.loads(content) if isinstance(content, str) else content
                    if isinstance(payload, dict) and payload.get("intercepted"):
                        intercepted_review = json.dumps(payload, indent=2)
                except (json.JSONDecodeError, TypeError):
                    pass

    return intercepted_review or agent_output


# ---------------------------------------------------------------------------
# LLM-as-judge
# ---------------------------------------------------------------------------

JUDGE_PROMPT = """You are evaluating whether an AI agent caught the same issues that Devin flagged in a pull request review.

## Expected Issues (ground truth from Devin)
{expected}

## Agent's Actual Review
{actual}

## Instructions

The expected review contains an `issues` list. For each expected issue, determine whether the agent's review catches it.

An issue counts as CAUGHT if the agent:
- References the same file (exact path match), AND
- Identifies the same conceptual bug or problem (doesn't need to match word-for-word, but must describe the same root cause)

An issue counts as MISSED if the agent does not mention the file at all, or mentions the file but describes an unrelated problem.

## Output format

For each expected issue, output one line:

ISSUE: [file path] — [CAUGHT|MISSED] — [one sentence reason]

Then output:

CAUGHT_COUNT: [number of CAUGHT issues]
TOTAL_COUNT: [total number of expected issues]
SUMMARY: [one sentence overall summary]"""


async def llm_judge(expected: dict, actual: str) -> dict[str, Any]:
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": JUDGE_PROMPT.format(
                    expected=json.dumps(expected, indent=2),
                    actual=actual,
                ),
            }
        ],
    )
    text = response.content[0].text

    # Parse counts
    caught_match = re.search(r"^CAUGHT_COUNT: (\d+)", text, re.MULTILINE)
    total_match = re.search(r"^TOTAL_COUNT: (\d+)", text, re.MULTILINE)
    summary_match = re.search(r"^SUMMARY: (.+)", text, re.MULTILINE)

    caught_count = int(caught_match.group(1)) if caught_match else 0
    total_count = int(total_match.group(1)) if total_match else len(expected.get("issues", []))
    summary = summary_match.group(1).strip() if summary_match else ""

    percentage_passed = round((caught_count / total_count) * 100) if total_count > 0 else 0
    result = "pass" if caught_count == total_count else "fail"

    # Extract per-issue details
    issue_lines = re.findall(r"^ISSUE: (.+)", text, re.MULTILINE)

    return {
        "result": result,
        "percentage_passed": percentage_passed,
        "caught_count": caught_count,
        "total_count": total_count,
        "issues": issue_lines,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.langsmith
async def test_pr_review(eval_entry: dict[str, Any]):
    inputs = {
        "pr_number": eval_entry["pr_number"],
        "pr_url": eval_entry["pr_url"],
        "pr_title": eval_entry["pr_title"],
        "expected_review": eval_entry["expected_review"],
    }
    t.log_inputs(inputs)

    agent_output = await run_agent_on_pr(eval_entry)
    scores = await llm_judge(eval_entry["expected_review"], agent_output)

    t.log_outputs({"agent_output": agent_output, **scores})

    # Save results locally
    results = json.loads(RESULTS_PATH.read_text()) if RESULTS_PATH.exists() else []
    results = [r for r in results if r.get("id") != eval_entry["id"]]  # replace existing
    results.append(
        {
            "id": eval_entry["id"],
            "pr_number": eval_entry["pr_number"],
            "agent_output": agent_output,
            **scores,
        }
    )
    RESULTS_PATH.write_text(json.dumps(results, indent=2))

    assert scores["result"] == "pass", (
        f"PR #{eval_entry['pr_number']} ({eval_entry['id']}): {scores['summary']}\n"
        f"percentage_passed={scores['percentage_passed']}% "
        f"({scores['caught_count']}/{scores['total_count']})"
    )
