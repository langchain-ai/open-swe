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
from langsmith.evaluation import EvaluationResult
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


def judge_match(run: Run, example: Example) -> EvaluationResult:
    """Per-example evaluator: compute precision/recall/f1 against golden comments."""
    candidates: list[dict] = list((run.outputs or {}).get("comments") or [])
    goldens: list[dict] = list((example.outputs or {}).get("golden_comments") or [])

    if not goldens:
        return EvaluationResult(key="f1", score=None, comment="no goldens")

    matched_goldens: set[int] = set()
    matched_candidates: set[int] = set()
    pair_results: list[dict] = []

    for ci, cand in enumerate(candidates):
        for gi, gold in enumerate(goldens):
            if gi in matched_goldens:
                continue
            res = _judge_pair(gold, cand)
            pair_results.append({"candidate_idx": ci, "golden_idx": gi, **res})
            if res.get("match"):
                matched_goldens.add(gi)
                matched_candidates.add(ci)
                break  # candidate consumed; move to next candidate

    tp = len(matched_goldens)
    fp = max(0, len(candidates) - len(matched_candidates))
    fn = max(0, len(goldens) - tp)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return EvaluationResult(
        key="f1",
        score=f1,
        comment=f"P={precision:.2f} R={recall:.2f} TP={tp} FP={fp} FN={fn}",
        evaluator_info={"model": JUDGE_MODEL},
        extra={
            "precision": precision,
            "recall": recall,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "n_candidates": len(candidates),
            "n_goldens": len(goldens),
            "pairs": pair_results,
        },
    )


def aggregate_pr(runs: list[Run], examples: list[Example]) -> list[EvaluationResult]:
    """Summary evaluator: micro- and macro-averaged precision/recall across the experiment."""
    micro_tp = micro_fp = micro_fn = 0
    p_macro: list[float] = []
    r_macro: list[float] = []

    for run in runs:
        feedback = next(
            (f for f in (run.feedback_stats or {}).values() if isinstance(f, dict)), None
        )
        # Pull per-example numbers off the run's evaluator extras when available.
        # We re-judge below if extras are unavailable to keep this evaluator pure.
        extras = None
        for ev in run.outputs and run.outputs.get("__evaluator_extras__", []) or []:
            if ev.get("key") == "f1":
                extras = ev.get("extra")
                break
        if not extras:
            continue
        micro_tp += extras["tp"]
        micro_fp += extras["fp"]
        micro_fn += extras["fn"]
        p_macro.append(extras["precision"])
        r_macro.append(extras["recall"])
        del feedback  # unused; reserved for future LangSmith API

    if not p_macro:
        return []

    micro_p = micro_tp / (micro_tp + micro_fp) if (micro_tp + micro_fp) else 0.0
    micro_r = micro_tp / (micro_tp + micro_fn) if (micro_tp + micro_fn) else 0.0
    macro_p = sum(p_macro) / len(p_macro)
    macro_r = sum(r_macro) / len(r_macro)

    def _f1(p: float, r: float) -> float:
        return 2 * p * r / (p + r) if (p + r) else 0.0

    return [
        EvaluationResult(key="micro_precision", score=micro_p),
        EvaluationResult(key="micro_recall", score=micro_r),
        EvaluationResult(key="micro_f1", score=_f1(micro_p, micro_r)),
        EvaluationResult(key="macro_precision", score=macro_p),
        EvaluationResult(key="macro_recall", score=macro_r),
        EvaluationResult(key="macro_f1", score=_f1(macro_p, macro_r)),
    ]
