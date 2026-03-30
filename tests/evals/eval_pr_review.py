"""PR Review evals — pytest-based, aligned with agent-builder eval pattern."""

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

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


def build_review_prompt(pr_url: str, pr_title: str, pr_body: str = "") -> str:
    """Build review prompt matching process_github_pr_ready_for_review in webapp.py exactly."""
    prompt = f"This PR has been marked ready for review.\n\nPR: {pr_url}\nTitle: {pr_title}\n"
    if pr_body:
        prompt += f"Description: {pr_body}\n"
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
    prompt = build_review_prompt(entry["pr_url"], entry["pr_title"], entry.get("pr_body", ""))

    configurable = {
        "source": "github",
        "github_login": "aran-yogesh",
        "github_user_id": 0,
        "repo": entry["input"]["configurable"]["repo"],
        "pr_number": entry["pr_number"],
        "review_mode": True,
        "eval_mode": True,
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

JUDGE_PROMPT = """You are evaluating whether an AI agent correctly reviewed a pull request.

## Expected Review (ground truth from Devin)
{expected}

## Agent's Actual Review
{actual}

Score on:
1. Recall — did the agent catch the critical issues? (FULL / PARTIAL / MISS)
2. Precision — did it add noise / false positives? (CLEAN / NOISY)
3. Verdict — did the agent's verdict match the expected `overall_verdict` in the ground truth? (CORRECT / WRONG)
   - The expected verdict is in the `overall_verdict` field of the Expected Review above.
   - CORRECT means the agent used the same verdict type (REQUEST_CHANGES / COMMENT / APPROVE).
   - Do NOT use your own judgment about what verdict was warranted — only compare against the expected.
4. Final score — use exactly these rules:
   - PASS: recall=FULL AND verdict=CORRECT
   - PARTIAL: recall=PARTIAL (any verdict); OR recall=MISS but agent raised at least some observations (even if wrong findings); OR recall=FULL with verdict=WRONG
   - FAIL: recall=MISS AND the agent's review is completely off-base with no relevant observations whatsoever

Respond as:
RECALL: [FULL|PARTIAL|MISS]
RECALL_REASON: ...
PRECISION: [CLEAN|NOISY]
VERDICT: [CORRECT|WRONG]
FINAL_SCORE: [PASS|PARTIAL|FAIL]
SUMMARY: one sentence"""


async def llm_judge(expected: dict, actual: str) -> dict[str, str]:
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
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

    def extract(key: str) -> str:
        m = re.search(rf"{key}: (.+)", text)
        return m.group(1).strip() if m else ""

    return {
        "recall": extract("RECALL"),
        "recall_reason": extract("RECALL_REASON"),
        "precision": extract("PRECISION"),
        "verdict": extract("VERDICT"),
        "final_score": extract("FINAL_SCORE"),
        "summary": extract("SUMMARY"),
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

    assert scores["final_score"] != "FAIL", (
        f"PR #{eval_entry['pr_number']} ({eval_entry['id']}): {scores['summary']}\n"
        f"recall={scores['recall']} | verdict={scores['verdict']}"
    )
