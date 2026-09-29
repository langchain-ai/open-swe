import asyncio
import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, TypeAdapter

REPO = "langchain-ai/open-swe"
CORRIDOR_LOGIN = "corridor-security[bot]"
OSWE_LOGIN = "open-swe[bot]"
CACHE_DIR = Path(__file__).parent / ".cache"

_GH_CONCURRENCY = asyncio.Semaphore(8)


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class User(_Model):
    login: str


class Review(_Model):
    id: int
    user: User | None
    state: str
    body: str
    commit_id: str | None
    submitted_at: datetime | None
    html_url: str


class ReviewComment(_Model):
    id: int
    user: User | None
    body: str
    path: str
    commit_id: str
    original_commit_id: str
    line: int | None
    original_line: int | None
    start_line: int | None
    original_start_line: int | None
    side: str | None
    diff_hunk: str
    in_reply_to_id: int | None = None
    pull_request_review_id: int | None
    created_at: datetime
    html_url: str


class CommitAuthor(_Model):
    date: datetime


class CommitDetail(_Model):
    message: str
    committer: CommitAuthor


class Commit(_Model):
    sha: str
    commit: CommitDetail


class Ref(_Model):
    ref: str
    sha: str


class PullRequest(_Model):
    number: int
    title: str
    html_url: str
    state: str
    created_at: datetime
    merged_at: datetime | None
    base: Ref
    head: Ref
    user: User | None


class PrBundle(_Model):
    pr: PullRequest
    reviews: list[Review]
    comments: list[ReviewComment]
    commits: list[Commit]
    merge_bases: dict[str, str | None]


async def gh_json(*args: str) -> object:
    async with _GH_CONCURRENCY:
        proc = await asyncio.create_subprocess_exec(
            "gh", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {stderr.decode().strip()}")
    return json.loads(stdout)


async def gh_list[T: BaseModel](path: str, model: type[T]) -> list[T]:
    pages = await gh_json("api", "--paginate", "--slurp", f"{path}?per_page=100")
    return [
        model.model_validate(item)
        for page in TypeAdapter(list[list[object]]).validate_python(pages)
        for item in page
    ]


def bundle_path(number: int) -> Path:
    return CACHE_DIR / "prs" / f"{number}.json"


def load_bundles() -> list[PrBundle]:
    return [
        PrBundle.model_validate_json(p.read_text())
        for p in sorted((CACHE_DIR / "prs").glob("*.json"))
    ]
