"""LLM-judge evaluator for the reviewer eval.

Pairwise matches each agent-emitted candidate against each golden comment using
claude-opus-4-5 (the model martian used to score Devin Review). Returns
precision/recall/f1 per example, plus aggregate metrics across the experiment.

The judge prompt is kept verbatim from
withmartian/code-review-benchmark `step3_judge_comments.py` so scores are
directly comparable to martian's published numbers.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_anthropic import ChatAnthropic
from langsmith.schemas import Example, Run

JUDGE_MODEL = "claude-opus-4-5"

JUDGE_SYSTEM = "You are a precise code review evaluator. Always respond with valid JSON."

JUDGE_PROMPT = """You are evaluating AI code review tools.
Determine if the candidate issue matches the golden (expected) comment.

Golden Comment (the issue we're looking for):
{golden_comment}

Candidate Issue (from the tool's review):
{candidate}

Instructions:
- Determine if the candidate identifies the SAME underlying issue as the golden comment
- Accept semantic matches - different wording is fine if it's the same problem
- Focus on whether they point to the same bug, concern, or code issue

Respond with ONLY a JSON object:
{{"reasoning": "brief explanation", "match": true/false, "confidence": 0.0-1.0}}"""


_judge: ChatAnthropic | None = None


def _get_judge() -> ChatAnthropic:
    global _judge
    if _judge is None:
        _judge = ChatAnthropic(model=JUDGE_MODEL, temperature=0.0, max_tokens=512)
    return _judge


def _format_candidate(c: dict) -> str:
    parts = []
    if c.get("file"):
        loc = c["file"]
        if c.get("line") is not None:
            loc += f":{c['line']}"
        parts.append(f"Location: {loc}")
    if c.get("severity"):
        parts.append(f"Severity: {c['severity']}")
    parts.append(f"Comment: {c.get('body') or c.get('comment') or ''}")
    return "\n".join(parts)


def _format_golden(g: dict) -> str:
    parts = []
    if g.get("severity"):
        parts.append(f"Severity: {g['severity']}")
    parts.append(f"Comment: {g.get('comment', '')}")
    return "\n".join(parts)


def _judge_pair(golden: dict, candidate: dict) -> dict[str, Any]:
    prompt = JUDGE_PROMPT.format(
        golden_comment=_format_golden(golden),
        candidate=_format_candidate(candidate),
    )
    msg = _get_judge().invoke(
        [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": prompt}]
    )
    raw = msg.content if isinstance(msg.content, str) else str(msg.content)
    try:
        start, end = raw.find("{"), raw.rfind("}")
        return json.loads(raw[start : end + 1])
    except (ValueError, json.JSONDecodeError):
        return {"match": False, "confidence": 0.0, "reasoning": f"unparseable: {raw[:200]}"}


def judge_match(run: Run, example: Example) -> dict[str, Any]:
    """Per-example evaluator: compute precision/recall/f1/tp/fp/fn against goldens.

    Returns a list of metrics under {"results": [...]}; LangSmith averages each
    numeric key across the experiment automatically — no separate summary
    evaluator needed.
    """
    candidates: list[dict] = list((run.outputs or {}).get("comments") or [])
    goldens: list[dict] = list((example.outputs or {}).get("golden_comments") or [])

    if not goldens:
        return {"results": [{"key": "f1", "score": None, "comment": "no goldens"}]}

    matched_goldens: set[int] = set()
    matched_candidates: set[int] = set()

    for ci, cand in enumerate(candidates):
        for gi, gold in enumerate(goldens):
            if gi in matched_goldens:
                continue
            res = _judge_pair(gold, cand)
            if res.get("match"):
                matched_goldens.add(gi)
                matched_candidates.add(ci)
                break

    tp = len(matched_goldens)
    fp = max(0, len(candidates) - len(matched_candidates))
    fn = max(0, len(goldens) - tp)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "results": [
            {"key": "f1", "score": f1},
            {"key": "precision", "score": precision},
            {"key": "recall", "score": recall},
            {"key": "tp", "score": tp},
            {"key": "fp", "score": fp},
            {"key": "fn", "score": fn},
            {"key": "n_candidates", "score": len(candidates)},
            {"key": "n_goldens", "score": len(goldens)},
        ]
    }
