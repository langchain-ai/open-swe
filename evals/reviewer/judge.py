"""LLM-judge evaluator for the reviewer eval.

Pairwise matches each agent-emitted candidate against each golden comment using
claude-opus-4-5 (the model martian used to score Devin Review). Returns
precision/recall/f1 per example, plus aggregate micro/macro metrics across
the experiment via a summary evaluator.

The judge prompt is kept verbatim from
withmartian/code-review-benchmark `step3_judge_comments.py` so scores are
directly comparable to martian's published numbers.
"""

from __future__ import annotations

import json
import threading
from typing import Any
from uuid import UUID

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


_PER_EXAMPLE_COUNTS: dict[UUID, dict[str, int | float]] = {}
_COUNTS_LOCK = threading.Lock()


def _record_counts(example_id: UUID, counts: dict[str, int | float]) -> None:
    with _COUNTS_LOCK:
        _PER_EXAMPLE_COUNTS[example_id] = counts


def _drain_counts() -> list[dict[str, int | float]]:
    with _COUNTS_LOCK:
        snapshot = list(_PER_EXAMPLE_COUNTS.values())
        _PER_EXAMPLE_COUNTS.clear()
    return snapshot


def judge_match(run: Run, example: Example) -> dict[str, Any]:
    """Per-example evaluator: compute precision/recall/f1/tp/fp/fn against goldens.

    Stashes the raw counts on a process-local cache keyed by ``example.id`` so
    ``aggregate_pr`` can compute micro-averages without re-judging.
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

    _record_counts(
        example.id,
        {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1},
    )

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


def _f1(p: float, r: float) -> float:
    return 2 * p * r / (p + r) if (p + r) else 0.0


def aggregate_pr(runs: list[Run], examples: list[Example]) -> dict[str, Any]:
    """Summary evaluator: micro/macro precision-recall-F1 across the experiment.

    Reads the per-example counts that ``judge_match`` stashed in the
    process-local cache. Falls back to an empty result set if the cache
    is empty (e.g. summary evaluator ran in a different process).
    """
    counts = _drain_counts()
    if not counts:
        return {"results": []}

    micro_tp = sum(int(c["tp"]) for c in counts)
    micro_fp = sum(int(c["fp"]) for c in counts)
    micro_fn = sum(int(c["fn"]) for c in counts)

    micro_p = micro_tp / (micro_tp + micro_fp) if (micro_tp + micro_fp) else 0.0
    micro_r = micro_tp / (micro_tp + micro_fn) if (micro_tp + micro_fn) else 0.0
    micro_f1 = _f1(micro_p, micro_r)

    n = len(counts)
    macro_p = sum(float(c["precision"]) for c in counts) / n
    macro_r = sum(float(c["recall"]) for c in counts) / n
    macro_f1 = sum(float(c["f1"]) for c in counts) / n

    return {
        "results": [
            {"key": "micro_precision", "score": micro_p},
            {"key": "micro_recall", "score": micro_r},
            {"key": "micro_f1", "score": micro_f1},
            {"key": "macro_precision", "score": macro_p},
            {"key": "macro_recall", "score": macro_r},
            {"key": "macro_f1", "score": macro_f1},
            {"key": "total_tp", "score": micro_tp},
            {"key": "total_fp", "score": micro_fp},
            {"key": "total_fn", "score": micro_fn},
        ]
    }
