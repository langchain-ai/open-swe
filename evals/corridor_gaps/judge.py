"""Label each Corridor finding with an LLM: category, triage, and which Open SWE comments match it.

Usage:
    uv run python -m evals.corridor_gaps.judge [--refresh] [--pr 3352]
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field

from evals.corridor_gaps.github import CACHE_DIR, PrBundle, load_bundles
from evals.corridor_gaps.parse import (
    Severity,
    corridor_findings,
    oswe_findings,
    oswe_reviews,
)

JUDGE_MODEL = "claude-opus-5-5"
PROMPT_PATH = Path(__file__).parent / "prompts" / "judge.md"
JUDGEMENTS_DIR = CACHE_DIR / "judgements"
_BODY_LIMIT = 4000
_LLM_CONCURRENCY = asyncio.Semaphore(6)

Category = Literal[
    "prompt_injection",
    "authorization",
    "credential_misuse",
    "secret_exposure",
    "data_exposure",
    "ci_supply_chain",
    "command_injection",
    "resource_exhaustion",
    "web_security",
    "ssrf",
    "data_integrity",
    "other",
]
Triage = Literal[
    "valid_fixed",
    "valid_accepted_risk",
    "valid_unresolved",
    "false_positive",
    "disputed",
    "no_response",
]


class FindingJudgement(BaseModel):
    corridor_comment_id: int
    issue_key: str
    title: str
    golden_comment: str
    category: Category
    cwe: str | None
    severity: Severity
    matched_oswe_comment_ids: list[int]
    near_miss_oswe_comment_ids: list[int]
    match_reasoning: str
    triage: Triage
    triage_evidence: str | None
    fix_commit: str | None
    detection_hint: str


class PrJudgement(BaseModel):
    judgements: list[FindingJudgement] = Field(
        description="One entry per Corridor finding, in input order"
    )


def _clip(text: str) -> str:
    return text if len(text) <= _BODY_LIMIT else text[:_BODY_LIMIT] + "…"


def build_context(bundle: PrBundle) -> str:
    context = {
        "pr": {
            "number": bundle.pr.number,
            "title": bundle.pr.title,
            "url": bundle.pr.html_url,
            "state": "merged" if bundle.pr.merged_at else bundle.pr.state,
        },
        "commits": [
            {
                "sha": c.sha,
                "date": c.commit.committer.date.isoformat(),
                "message": c.commit.message.splitlines()[0],
            }
            for c in bundle.commits
        ],
        "corridor_findings": [
            {
                "corridor_comment_id": f.comment_id,
                "commit_sha": f.commit_sha,
                "created_at": f.created_at.isoformat(),
                "path": f.path,
                "lines": [f.start_line, f.line],
                "review_summary": f.summary,
                "body": _clip(f.body),
                "replies": [{"login": r.login, "body": _clip(r.body)} for r in f.replies],
            }
            for f in corridor_findings(bundle)
        ],
        "oswe_reviews": [
            {
                "commit_sha": r.commit_sha,
                "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
                "outcome": r.outcome,
                "finding_count": r.finding_count,
            }
            for r in oswe_reviews(bundle)
        ],
        "oswe_findings": [
            {
                "comment_id": f.comment_id,
                "commit_sha": f.commit_sha,
                "created_at": f.created_at.isoformat(),
                "path": f.path,
                "line": f.line,
                "severity": f.severity,
                "body": _clip(f.body),
            }
            for f in oswe_findings(bundle)
        ],
    }
    return json.dumps(context, indent=1)


def judgement_path(number: int) -> Path:
    return JUDGEMENTS_DIR / f"{number}.json"


def render_prompt(bundle: PrBundle) -> str:
    return PROMPT_PATH.read_text().replace("{context}", build_context(bundle))


def validate(bundle: PrBundle, result: PrJudgement) -> None:
    expected = [f.comment_id for f in corridor_findings(bundle)]
    got = [j.corridor_comment_id for j in result.judgements]
    if sorted(got) != sorted(expected):
        raise RuntimeError(f"PR #{bundle.pr.number}: judged {got}, expected {expected}")
    oswe_ids = {f.comment_id for f in oswe_findings(bundle)}
    for j in result.judgements:
        unknown = set(j.matched_oswe_comment_ids + j.near_miss_oswe_comment_ids) - oswe_ids
        if unknown:
            raise RuntimeError(f"PR #{bundle.pr.number}: unknown oswe comment ids {unknown}")


def load_judgement(bundle: PrBundle) -> PrJudgement:
    result = PrJudgement.model_validate_json(judgement_path(bundle.pr.number).read_text())
    validate(bundle, result)
    return result


async def judge_pr(llm: ChatAnthropic, bundle: PrBundle, refresh: bool) -> None:
    path = judgement_path(bundle.pr.number)
    if path.exists() and not refresh:
        return
    structured = llm.with_structured_output(PrJudgement, method="json_schema")
    async with _LLM_CONCURRENCY:
        result = PrJudgement.model_validate(await structured.ainvoke(render_prompt(bundle)))
    validate(bundle, result)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=1))
    print(f"  #{bundle.pr.number}: {len(result.judgements)} judged")


def emit_prompts(bundles: list[PrBundle]) -> None:
    out = CACHE_DIR / "prompts"
    out.mkdir(parents=True, exist_ok=True)
    for b in bundles:
        (out / f"{b.pr.number}.md").write_text(render_prompt(b))
    (out / "schema.json").write_text(json.dumps(PrJudgement.model_json_schema(), indent=1))
    print(f"Wrote {len(bundles)} prompts and schema.json to {out}")


def check(bundles: list[PrBundle]) -> None:
    missing = [b.pr.number for b in bundles if not judgement_path(b.pr.number).exists()]
    for b in bundles:
        if b.pr.number not in missing:
            load_judgement(b)
    print(f"{len(bundles) - len(missing)} judgements valid; missing: {missing}")


async def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--pr", type=int, action="append")
    ap.add_argument(
        "--emit-prompts",
        action="store_true",
        help="Write rendered prompts for an external judge instead of calling the API",
    )
    ap.add_argument("--check", action="store_true", help="Validate existing judgement files")
    args = ap.parse_args()
    bundles = [b for b in load_bundles() if not args.pr or b.pr.number in args.pr]
    if args.emit_prompts:
        emit_prompts(bundles)
        return
    if args.check:
        check(bundles)
        return
    llm = ChatAnthropic(
        model=JUDGE_MODEL,
        max_tokens=16000,
        api_key=os.environ["ANTHROPIC_API_KEY"],
        max_retries=3,
        timeout=600,
    )
    results = await asyncio.gather(
        *(judge_pr(llm, b, args.refresh) for b in bundles), return_exceptions=True
    )
    failures = [
        (b.pr.number, r)
        for b, r in zip(bundles, results, strict=True)
        if isinstance(r, BaseException)
    ]
    for number, exc in failures:
        print(f"  #{number} FAILED: {exc!r}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
