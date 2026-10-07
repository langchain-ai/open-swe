"""LLM-judge evaluator for the reviewer eval.

Pairwise matches each agent-emitted candidate against each golden comment using
claude-opus-4-5 (the judge model used by the martian benchmark). Returns
precision/recall/f1 per example, plus aggregate micro/macro metrics across
the experiment via a summary evaluator.

The unprefixed metrics count every golden (martian's "all" profile). The
``strict_`` and ``core_`` metrics follow martian's category profiles
(`analysis/score_profiles.py`); martian's headline is ``core_micro_f2``.

The judge prompt is kept verbatim from
withmartian/code-review-benchmark `step3_judge_comments.py` so scores are
directly comparable to martian's published numbers.
"""

import json
import threading
from functools import cache
from typing import Any, Literal, NotRequired, TypedDict
from uuid import UUID

from langchain_core.language_models import BaseChatModel
from langsmith.schemas import Example, Run

from openswe.utils.gateway import gateway_overrides
from openswe.utils.model import make_model

JUDGE_MODEL = "anthropic:claude-opus-4-5"

ScoringProfile = Literal["strict", "core"]
_STRICT_CATEGORIES = frozenset({"bug", "security", "concurrency", "data", "api"})
PROFILE_CATEGORIES: dict[ScoringProfile, frozenset[str]] = {
    "strict": _STRICT_CATEGORIES,
    "core": _STRICT_CATEGORIES | {"perf", "test_gap", "doc_defect"},
}

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


_judge: BaseChatModel | None = None


class ReviewComment(TypedDict):
    comment: NotRequired[str]
    body: NotRequired[str]
    file: NotRequired[str]
    line: NotRequired[int | None]
    severity: NotRequired[str]
    category: NotRequired[str]


class PairResult(TypedDict):
    match: bool
    confidence: float
    reasoning: str


class MatrixCell(PairResult):
    candidate_index: int
    golden_index: int


class ProfileCounts(TypedDict):
    tp: int
    fp: int
    fn: int


class ExampleCounts(TypedDict):
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    medium_plus_tp: int
    medium_plus_fp: int
    medium_plus_fn: int
    medium_plus_precision: float
    medium_plus_recall: float
    medium_plus_f1: float
    profiles: dict[ScoringProfile, ProfileCounts]
    is_synthetic: bool


def _get_judge() -> BaseChatModel:
    global _judge
    if _judge is None:
        # Without gateway credentials make_model silently calls Anthropic directly.
        if gateway_overrides(JUDGE_MODEL) is None:
            raise RuntimeError(
                "The judge calls Anthropic through the LangSmith gateway; set "
                "LANGSMITH_GATEWAY_API_KEY or LANGSMITH_API_KEY."
            )
        _judge = make_model(
            JUDGE_MODEL, use_gateway=True, temperature=0.0, max_tokens=512, max_retries=3
        )
    return _judge


def _format_candidate(c: ReviewComment) -> str:
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


def _format_golden(g: ReviewComment) -> str:
    parts = []
    if g.get("severity"):
        parts.append(f"Severity: {g['severity']}")
    parts.append(f"Comment: {g.get('comment', '')}")
    return "\n".join(parts)


def _judge_pair(golden: ReviewComment, candidate: ReviewComment) -> PairResult:
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
        parsed = json.loads(raw[start : end + 1])
    except ValueError, json.JSONDecodeError:
        return {"match": False, "confidence": 0.0, "reasoning": f"unparseable: {raw[:200]}"}
    if not isinstance(parsed, dict):
        return {"match": False, "confidence": 0.0, "reasoning": "judge returned non-object"}
    match = parsed.get("match")
    confidence = parsed.get("confidence")
    reasoning = parsed.get("reasoning")
    return {
        "match": match if isinstance(match, bool) else False,
        "confidence": (
            min(max(float(confidence), 0.0), 1.0)
            if isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
            else 0.0
        ),
        "reasoning": reasoning if isinstance(reasoning, str) else "",
    }


_PER_EXAMPLE_COUNTS: dict[UUID, ExampleCounts] = {}
_COUNTS_LOCK = threading.Lock()


def _record_counts(example_id: UUID, counts: ExampleCounts) -> None:
    with _COUNTS_LOCK:
        _PER_EXAMPLE_COUNTS[example_id] = counts


def _drain_counts() -> list[ExampleCounts]:
    with _COUNTS_LOCK:
        snapshot = list(_PER_EXAMPLE_COUNTS.values())
        _PER_EXAMPLE_COUNTS.clear()
    return snapshot


def _coerce_comments(value: object) -> list[ReviewComment]:
    if not isinstance(value, list):
        return []
    comments: list[ReviewComment] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        comment: ReviewComment = {}
        comment_text = item.get("comment")
        if isinstance(comment_text, str):
            comment["comment"] = comment_text
        body = item.get("body")
        if isinstance(body, str):
            comment["body"] = body
        file = item.get("file")
        if isinstance(file, str):
            comment["file"] = file
        severity = item.get("severity")
        if isinstance(severity, str):
            comment["severity"] = severity
        category = item.get("category")
        if isinstance(category, str):
            comment["category"] = category
        line = item.get("line")
        if isinstance(line, int) or line is None:
            comment["line"] = line
        comments.append(comment)
    return comments


def _dedupe_candidates(candidates: list[ReviewComment]) -> tuple[list[ReviewComment], int]:
    unique: list[ReviewComment] = []
    seen: set[tuple[str, int | None, str]] = set()
    for candidate in candidates:
        key = (
            candidate.get("file", ""),
            candidate.get("line"),
            " ".join((candidate.get("body") or candidate.get("comment") or "").casefold().split()),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique, len(candidates) - len(unique)


def _build_matrix(
    candidates: list[ReviewComment], goldens: list[ReviewComment]
) -> list[list[PairResult]]:
    return [[_judge_pair(golden, candidate) for golden in goldens] for candidate in candidates]


def _select_pairs(matrix: list[list[PairResult]]) -> tuple[tuple[int, int], ...]:
    golden_count = len(matrix[0]) if matrix else 0

    @cache
    def _solve(
        candidate_index: int, matched_mask: int
    ) -> tuple[int, float, tuple[tuple[int, int], ...]]:
        if candidate_index >= len(matrix):
            return 0, 0.0, ()
        best = _solve(candidate_index + 1, matched_mask)
        for golden_index in range(golden_count):
            if matched_mask & (1 << golden_index):
                continue
            cell = matrix[candidate_index][golden_index]
            if not cell["match"]:
                continue
            count, confidence, pairs = _solve(
                candidate_index + 1, matched_mask | (1 << golden_index)
            )
            candidate = (
                count + 1,
                confidence + cell["confidence"],
                ((candidate_index, golden_index), *pairs),
            )
            if candidate[:2] > best[:2]:
                best = candidate
        return best

    return _solve(0, 0)[2]


def _metrics(
    tp: int, candidate_count: int, golden_count: int
) -> tuple[int, int, float, float, float]:
    fp = max(0, candidate_count - tp)
    fn = max(0, golden_count - tp)
    precision = tp / candidate_count if candidate_count else 0.0
    recall = tp / golden_count if golden_count else 0.0
    return fp, fn, precision, recall, _fbeta(precision, recall)


def _profile_counts(
    goldens: list[ReviewComment],
    matched_goldens: set[int],
    fp: int,
    categories: frozenset[str],
) -> ProfileCounts:
    # A match on an out-of-profile golden is neither a TP nor an FP, as upstream.
    in_profile = [i for i, golden in enumerate(goldens) if golden.get("category") in categories]
    tp = sum(1 for i in in_profile if i in matched_goldens)
    return {"tp": tp, "fp": fp, "fn": len(in_profile) - tp}


def _precision_recall(counts: ProfileCounts) -> tuple[float, float]:
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    return (tp / (tp + fp) if tp + fp else 0.0, tp / (tp + fn) if tp + fn else 0.0)


def _is_medium_plus(comment: ReviewComment) -> bool:
    return comment.get("severity", "").casefold() in {"medium", "high", "critical"}


def judge_match(run: Run, example: Example) -> dict[str, Any]:
    """Judge every pair, then choose the strongest maximum-cardinality matching."""
    raw_candidates = _coerce_comments((run.outputs or {}).get("comments"))
    candidates, duplicate_count = _dedupe_candidates(raw_candidates)
    goldens = _coerce_comments((example.outputs or {}).get("golden_comments"))

    if not goldens:
        return {"results": [{"key": "f1", "score": None, "comment": "no goldens"}]}

    matrix = _build_matrix(candidates, goldens)
    selected_pairs = _select_pairs(matrix)
    tp = len(selected_pairs)
    fp, fn, precision, recall, f1 = _metrics(tp, len(candidates), len(goldens))

    medium_candidate_indices = [
        i for i, candidate in enumerate(candidates) if _is_medium_plus(candidate)
    ]
    medium_golden_indices = [i for i, golden in enumerate(goldens) if _is_medium_plus(golden)]
    medium_matrix = [
        [matrix[candidate_index][golden_index] for golden_index in medium_golden_indices]
        for candidate_index in medium_candidate_indices
    ]
    medium_tp = len(_select_pairs(medium_matrix))
    medium_fp, medium_fn, medium_precision, medium_recall, medium_f1 = _metrics(
        medium_tp, len(medium_candidate_indices), len(medium_golden_indices)
    )
    matched_goldens = {golden_index for _, golden_index in selected_pairs}
    profiles: dict[ScoringProfile, ProfileCounts] = {
        name: _profile_counts(goldens, matched_goldens, fp, categories)
        for name, categories in PROFILE_CATEGORIES.items()
    }
    repo = (example.inputs or {}).get("repo")
    is_synthetic = isinstance(repo, str) and repo.startswith("ai-code-review-evaluation/")

    _record_counts(
        example.id,
        {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "medium_plus_tp": medium_tp,
            "medium_plus_fp": medium_fp,
            "medium_plus_fn": medium_fn,
            "medium_plus_precision": medium_precision,
            "medium_plus_recall": medium_recall,
            "medium_plus_f1": medium_f1,
            "profiles": profiles,
            "is_synthetic": is_synthetic,
        },
    )

    selected = set(selected_pairs)
    cells: list[MatrixCell] = []
    for candidate_index, row in enumerate(matrix):
        for golden_index, result in enumerate(row):
            cells.append(
                {
                    "candidate_index": candidate_index,
                    "golden_index": golden_index,
                    **result,
                }
            )
    matrix_feedback = {
        "candidates": candidates,
        "goldens": goldens,
        "cells": cells,
        "selected_pairs": [
            {"candidate_index": candidate_index, "golden_index": golden_index}
            for candidate_index, golden_index in selected_pairs
        ],
        "unmatched_candidates": [
            {"candidate_index": index, "candidate": candidate}
            for index, candidate in enumerate(candidates)
            if not any(pair[0] == index for pair in selected)
        ],
    }
    profile_results: list[dict[str, Any]] = []
    for name, counts in profiles.items():
        profile_precision, profile_recall = _precision_recall(counts)
        profile_results.extend(
            [
                {"key": f"{name}_precision", "score": profile_precision},
                {"key": f"{name}_recall", "score": profile_recall},
                {"key": f"{name}_f2", "score": _fbeta(profile_precision, profile_recall, 2.0)},
            ]
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
            {"key": "n_candidates_raw", "score": len(raw_candidates)},
            {"key": "n_duplicates", "score": duplicate_count},
            {"key": "n_goldens", "score": len(goldens)},
            {"key": "medium_plus_f1", "score": medium_f1},
            {"key": "medium_plus_precision", "score": medium_precision},
            {"key": "medium_plus_recall", "score": medium_recall},
            *profile_results,
            {"key": "pairwise_match_matrix", "value": json.dumps(matrix_feedback)},
        ]
    }


def _fbeta(p: float, r: float, beta: float = 1.0) -> float:
    beta2 = beta * beta
    denom = beta2 * p + r
    return (1 + beta2) * p * r / denom if denom else 0.0


def aggregate_pr(runs: list[Run], examples: list[Example]) -> dict[str, Any]:
    """Summary evaluator: micro/macro precision-recall-F1 across the experiment.

    Reads the per-example counts that ``judge_match`` stashed in the
    process-local cache. Falls back to an empty result set if the cache
    is empty (e.g. summary evaluator ran in a different process).
    """
    counts = _drain_counts()
    if not counts:
        return {"results": []}

    results = _aggregate_metrics(counts)
    results.extend(
        _aggregate_metrics(counts, key_prefix="medium_plus_", field_prefix="medium_plus_")
    )
    for profile in PROFILE_CATEGORIES:
        results.extend(_aggregate_profile(counts, profile))
    synthetic = [count for count in counts if count["is_synthetic"]]
    upstream = [count for count in counts if not count["is_synthetic"]]
    if synthetic:
        results.extend(_aggregate_metrics(synthetic, key_prefix="synthetic_"))
    if upstream:
        results.extend(_aggregate_metrics(upstream, key_prefix="upstream_"))
    return {"results": results}


def _aggregate_metrics(
    counts: list[ExampleCounts],
    *,
    key_prefix: str = "",
    field_prefix: str = "",
) -> list[dict[str, Any]]:
    tp_key = f"{field_prefix}tp"
    fp_key = f"{field_prefix}fp"
    fn_key = f"{field_prefix}fn"
    precision_key = f"{field_prefix}precision"
    recall_key = f"{field_prefix}recall"
    f1_key = f"{field_prefix}f1"
    micro_tp = sum(int(count[tp_key]) for count in counts)
    micro_fp = sum(int(count[fp_key]) for count in counts)
    micro_fn = sum(int(count[fn_key]) for count in counts)
    micro_precision = micro_tp / (micro_tp + micro_fp) if micro_tp + micro_fp else 0.0
    micro_recall = micro_tp / (micro_tp + micro_fn) if micro_tp + micro_fn else 0.0
    count = len(counts)
    return [
        {"key": f"{key_prefix}micro_precision", "score": micro_precision},
        {"key": f"{key_prefix}micro_recall", "score": micro_recall},
        {"key": f"{key_prefix}micro_f1", "score": _fbeta(micro_precision, micro_recall)},
        {"key": f"{key_prefix}micro_f2", "score": _fbeta(micro_precision, micro_recall, 2.0)},
        {
            "key": f"{key_prefix}macro_precision",
            "score": sum(float(item[precision_key]) for item in counts) / count,
        },
        {
            "key": f"{key_prefix}macro_recall",
            "score": sum(float(item[recall_key]) for item in counts) / count,
        },
        {
            "key": f"{key_prefix}macro_f1",
            "score": sum(float(item[f1_key]) for item in counts) / count,
        },
        {"key": f"{key_prefix}total_tp", "score": micro_tp},
        {"key": f"{key_prefix}total_fp", "score": micro_fp},
        {"key": f"{key_prefix}total_fn", "score": micro_fn},
    ]


def _aggregate_profile(
    counts: list[ExampleCounts], profile: ScoringProfile
) -> list[dict[str, Any]]:
    total: ProfileCounts = {
        "tp": sum(count["profiles"][profile]["tp"] for count in counts),
        "fp": sum(count["profiles"][profile]["fp"] for count in counts),
        "fn": sum(count["profiles"][profile]["fn"] for count in counts),
    }
    precision, recall = _precision_recall(total)
    return [
        {"key": f"{profile}_micro_precision", "score": precision},
        {"key": f"{profile}_micro_recall", "score": recall},
        {"key": f"{profile}_micro_f1", "score": _fbeta(precision, recall)},
        {"key": f"{profile}_micro_f2", "score": _fbeta(precision, recall, 2.0)},
        {"key": f"{profile}_total_tp", "score": total["tp"]},
        {"key": f"{profile}_total_fp", "score": total["fp"]},
        {"key": f"{profile}_total_fn", "score": total["fn"]},
    ]
