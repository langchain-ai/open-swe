"""Assemble the Corridor-gap dataset from cached GitHub data and judgements.

Usage:
    uv run python -m evals.corridor_gaps.build
"""

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from evals.corridor_gaps.github import REPO, PrBundle, load_bundles
from evals.corridor_gaps.judge import Category, FindingJudgement, Triage, load_judgement
from evals.corridor_gaps.parse import (
    CorridorFinding,
    OsweFinding,
    OsweReview,
    Reply,
    Severity,
    commit_order,
    corridor_findings,
    oswe_findings,
    oswe_reviews,
)

DATA_DIR = Path(__file__).parent / "data"

Detection = Literal[
    "caught_same_commit",
    "caught_earlier_commit",
    "caught_later_commit",
    "missed_on_same_commit",
    "missed_other_commits_only",
    "not_reviewed",
]


class PrInfo(BaseModel):
    repo: str
    number: int
    title: str
    url: str
    author: str | None
    created_at: datetime
    state: Literal["open", "closed", "merged"]
    base_ref: str
    head_ref: str
    final_head_sha: str
    commit_count: int


class CommitInfo(BaseModel):
    head_sha: str
    base_sha: str | None
    index: int | None
    is_final_head: bool
    force_pushed_away: bool


class Location(BaseModel):
    path: str
    start_line: int | None
    line: int | None
    side: str | None
    diff_hunk: str


class Corridor(BaseModel):
    comment_id: int
    url: str
    created_at: datetime
    finding_id: str | None
    finding_url: str | None
    review_summary: str | None
    body: str


class Label(BaseModel):
    issue_key: str
    is_first_occurrence: bool
    title: str
    golden_comment: str
    category: Category
    cwe: str | None
    severity: Severity
    detection_hint: str


class Triaged(BaseModel):
    verdict: Triage
    evidence: str | None
    fix_commit: str | None
    replies: list[Reply]


class OsweCoverage(BaseModel):
    detection: Detection
    match_reasoning: str
    reviewed_pr: bool
    reviewed_corridor_commit: bool
    reviews_before: int
    reviews_at: int
    reviews_after: int
    matched: list[OsweFinding]
    near_misses: list[OsweFinding]
    reviews: list[OsweReview]


class FindingRecord(BaseModel):
    id: str
    pr: PrInfo
    commit: CommitInfo
    location: Location
    corridor: Corridor
    label: Label
    triage: Triaged
    oswe: OsweCoverage


class GoldenComment(BaseModel):
    comment: str
    severity: str
    file: str
    line: int | None
    title: str
    category: Category
    issue_key: str
    finding_id: str
    oswe_detection: Detection


class ExampleInputs(BaseModel):
    repo: str
    pr_number: int
    pr_url: str
    pr_title: str
    base_sha: str
    head_sha: str
    base_ref: str
    head_ref: str


class ExampleOutputs(BaseModel):
    golden_comments: list[GoldenComment]


class ExampleMetadata(BaseModel):
    source: Literal["corridor"]
    pr_state: str
    commit_index: int | None
    is_final_head: bool
    oswe_missed_count: int
    oswe_caught_count: int


class Example(BaseModel):
    inputs: ExampleInputs
    outputs: ExampleOutputs
    metadata: ExampleMetadata


def _detection(
    bundle: PrBundle,
    finding: CorridorFinding,
    matched: list[OsweFinding],
    reviews: list[OsweReview],
) -> Detection:
    if not matched:
        if not reviews:
            return "not_reviewed"
        if any(r.commit_sha == finding.commit_sha for r in reviews):
            return "missed_on_same_commit"
        return "missed_other_commits_only"
    if any(m.commit_sha == finding.commit_sha for m in matched):
        return "caught_same_commit"
    order = commit_order(bundle)
    first = min(matched, key=lambda m: m.created_at)
    if first.commit_sha in order and finding.commit_sha in order:
        earlier = order[first.commit_sha] < order[finding.commit_sha]
    else:
        earlier = first.created_at < finding.created_at
    return "caught_earlier_commit" if earlier else "caught_later_commit"


def _review_counts(
    bundle: PrBundle, finding: CorridorFinding, reviews: list[OsweReview]
) -> tuple[int, int, int]:
    order = commit_order(bundle)
    target = order.get(finding.commit_sha)
    before = at = after = 0
    for r in reviews:
        if r.commit_sha == finding.commit_sha:
            at += 1
        elif target is not None and r.commit_sha in order:
            if order[r.commit_sha] < target:
                before += 1
            else:
                after += 1
        elif r.submitted_at and r.submitted_at < finding.created_at:
            before += 1
        else:
            after += 1
    return before, at, after


def build_pr(bundle: PrBundle) -> list[FindingRecord]:
    judgements: dict[int, FindingJudgement] = {
        j.corridor_comment_id: j for j in load_judgement(bundle).judgements
    }
    by_id = {f.comment_id: f for f in oswe_findings(bundle)}
    reviews = oswe_reviews(bundle)
    order = commit_order(bundle)
    pr = bundle.pr
    info = PrInfo(
        repo=REPO,
        number=pr.number,
        title=pr.title,
        url=pr.html_url,
        author=pr.user.login if pr.user else None,
        created_at=pr.created_at,
        state="merged" if pr.merged_at else ("open" if pr.state == "open" else "closed"),
        base_ref=pr.base.ref,
        head_ref=pr.head.ref,
        final_head_sha=pr.head.sha,
        commit_count=len(bundle.commits),
    )
    seen_keys: set[str] = set()
    records: list[FindingRecord] = []
    for f in corridor_findings(bundle):
        j = judgements[f.comment_id]
        matched = [by_id[i] for i in j.matched_oswe_comment_ids]
        before, at, after = _review_counts(bundle, f, reviews)
        records.append(
            FindingRecord(
                id=f"{REPO}#{pr.number}/{f.comment_id}",
                pr=info,
                commit=CommitInfo(
                    head_sha=f.commit_sha,
                    base_sha=bundle.merge_bases.get(f.commit_sha),
                    index=order.get(f.commit_sha),
                    is_final_head=f.commit_sha == pr.head.sha,
                    force_pushed_away=f.commit_sha not in order,
                ),
                location=Location(
                    path=f.path,
                    start_line=f.start_line,
                    line=f.line,
                    side=f.side,
                    diff_hunk=f.diff_hunk,
                ),
                corridor=Corridor(
                    comment_id=f.comment_id,
                    url=f.url,
                    created_at=f.created_at,
                    finding_id=f.corridor_finding_id,
                    finding_url=f.corridor_finding_url,
                    review_summary=f.summary,
                    body=f.body,
                ),
                label=Label(
                    issue_key=j.issue_key,
                    is_first_occurrence=j.issue_key not in seen_keys,
                    title=j.title,
                    golden_comment=j.golden_comment,
                    category=j.category,
                    cwe=j.cwe,
                    severity=j.severity,
                    detection_hint=j.detection_hint,
                ),
                triage=Triaged(
                    verdict=j.triage,
                    evidence=j.triage_evidence,
                    fix_commit=j.fix_commit,
                    replies=f.replies,
                ),
                oswe=OsweCoverage(
                    detection=_detection(bundle, f, matched, reviews),
                    match_reasoning=j.match_reasoning,
                    reviewed_pr=bool(reviews),
                    reviewed_corridor_commit=at > 0,
                    reviews_before=before,
                    reviews_at=at,
                    reviews_after=after,
                    matched=matched,
                    near_misses=[by_id[i] for i in j.near_miss_oswe_comment_ids],
                    reviews=reviews,
                ),
            )
        )
        seen_keys.add(j.issue_key)
    return records


def build_examples(records: list[FindingRecord]) -> list[Example]:
    groups: dict[tuple[int, str], list[FindingRecord]] = {}
    for r in records:
        if (
            not r.label.is_first_occurrence
            or r.triage.verdict == "false_positive"
            or not r.commit.base_sha
        ):
            continue
        groups.setdefault((r.pr.number, r.commit.head_sha), []).append(r)
    examples: list[Example] = []
    for (_, head_sha), rs in sorted(groups.items()):
        first = rs[0]
        missed = sum(r.oswe.detection.startswith("missed") for r in rs)
        examples.append(
            Example(
                inputs=ExampleInputs(
                    repo=first.pr.repo,
                    pr_number=first.pr.number,
                    pr_url=first.pr.url,
                    pr_title=first.pr.title,
                    base_sha=first.commit.base_sha or "",
                    head_sha=head_sha,
                    base_ref=first.pr.base_ref,
                    head_ref=first.pr.head_ref,
                ),
                outputs=ExampleOutputs(
                    golden_comments=[
                        GoldenComment(
                            comment=r.label.golden_comment,
                            severity=r.label.severity.capitalize(),
                            file=r.location.path,
                            line=r.location.line,
                            title=r.label.title,
                            category=r.label.category,
                            issue_key=r.label.issue_key,
                            finding_id=r.id,
                            oswe_detection=r.oswe.detection,
                        )
                        for r in rs
                    ]
                ),
                metadata=ExampleMetadata(
                    source="corridor",
                    pr_state=first.pr.state,
                    commit_index=first.commit.index,
                    is_final_head=first.commit.is_final_head,
                    oswe_missed_count=missed,
                    oswe_caught_count=sum(r.oswe.detection.startswith("caught") for r in rs),
                ),
            )
        )
    return examples


def _table(title: str, counts: Counter[str]) -> str:
    rows = "\n".join(f"| {k} | {v} |" for k, v in counts.most_common())
    return f"### {title}\n\n| value | count |\n|---|---|\n{rows}\n"


def summary(records: list[FindingRecord], examples: list[Example]) -> str:
    issues = [r for r in records if r.label.is_first_occurrence]
    real = [r for r in issues if r.triage.verdict != "false_positive"]
    reviewed = [r for r in real if r.oswe.detection != "not_reviewed"]
    missed = [r for r in reviewed if r.oswe.detection.startswith("missed")]
    missed_lines = "\n".join(
        f"- [{r.pr.title} (#{r.pr.number})]({r.corridor.url}): **{r.label.title}**"
        f" ({r.label.category}, {r.label.severity}, {r.triage.verdict}, {r.oswe.detection})"
        for r in sorted(
            missed, key=lambda r: ("critical", "high", "medium", "low").index(r.label.severity)
        )
    )
    return "\n".join(
        [
            "# Corridor findings vs the Open SWE reviewer",
            "",
            f"- PRs: {len({r.pr.number for r in records})}",
            f"- Corridor comments: {len(records)}; distinct issues: {len(issues)}",
            f"- Distinct issues not marked false positive: {len(real)}",
            f"- Of those on PRs Open SWE reviewed: {len(reviewed)}, missed: {len(missed)}",
            f"- Eval examples (PR × commit): {len(examples)}",
            "",
            _table(
                "Open SWE detection (distinct issues)", Counter(r.oswe.detection for r in issues)
            ),
            _table("Category (distinct issues)", Counter(r.label.category for r in issues)),
            _table("Category of misses on reviewed PRs", Counter(r.label.category for r in missed)),
            _table("Triage (distinct issues)", Counter(r.triage.verdict for r in issues)),
            _table("Severity (distinct issues)", Counter(r.label.severity for r in issues)),
            "## Misses on reviewed PRs\n",
            missed_lines,
            "",
        ]
    )


def main() -> None:
    bundles = sorted(load_bundles(), key=lambda b: b.pr.number)
    records = [r for b in bundles for r in build_pr(b)]
    examples = build_examples(records)
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "findings.jsonl").write_text("".join(r.model_dump_json() + "\n" for r in records))
    (DATA_DIR / "examples.jsonl").write_text("".join(e.model_dump_json() + "\n" for e in examples))
    (DATA_DIR / "summary.md").write_text(summary(records, examples))
    print(f"{len(records)} findings, {len(examples)} examples written to {DATA_DIR}")


if __name__ == "__main__":
    main()
