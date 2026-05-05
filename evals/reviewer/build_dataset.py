"""Build the LangSmith dataset for the reviewer eval.

Reads golden_comments/*.json (martian offline benchmark goldens), resolves
each PR's base/head SHAs via `gh`, and uploads one example per PR to a
LangSmith dataset.

Usage:
    uv run python -m evals.reviewer.build_dataset \\
        --dataset-name openswe-reviewer-v1
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from langsmith import Client

load_dotenv()

GOLDENS_DIR = Path(__file__).parent / "golden_comments"
PR_URL_RE = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")


def parse_pr_url(url: str) -> tuple[str, str, int]:
    m = PR_URL_RE.search(url)
    if not m:
        raise ValueError(f"Not a PR url: {url}")
    owner, repo, num = m.group(1), m.group(2), int(m.group(3))
    return owner, repo.removesuffix(".git"), num


def gh_pr_view(owner: str, repo: str, pr_number: int) -> dict:
    result = subprocess.run(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            f"{owner}/{repo}",
            "--json",
            "baseRefOid,headRefOid,baseRefName,headRefName,title,state,url",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh pr view failed for {owner}/{repo}#{pr_number}: {result.stderr.strip()}"
        )
    return json.loads(result.stdout)


def load_goldens() -> list[dict]:
    examples: list[dict] = []
    for path in sorted(GOLDENS_DIR.glob("*.json")):
        with path.open() as f:
            entries = json.load(f)
        for entry in entries:
            entry["_source_file"] = path.stem
            examples.append(entry)
    return examples


def build_example(entry: dict) -> dict:
    url = entry["url"]
    owner, repo, pr_number = parse_pr_url(url)
    pr = gh_pr_view(owner, repo, pr_number)
    inputs = {
        "repo": f"{owner}/{repo}",
        "pr_number": pr_number,
        "pr_url": url,
        "original_url": entry.get("original_url"),
        "pr_title": entry.get("pr_title") or pr.get("title"),
        "base_sha": pr["baseRefOid"],
        "head_sha": pr["headRefOid"],
        "base_ref": pr["baseRefName"],
        "head_ref": pr["headRefName"],
    }
    outputs = {"golden_comments": entry["comments"]}
    metadata = {
        "source_file": entry["_source_file"],
        "az_comment": entry.get("az_comment"),
        "pr_state": pr.get("state"),
    }
    return {"inputs": inputs, "outputs": outputs, "metadata": metadata}


def upload(dataset_name: str, examples: list[dict]) -> None:
    client = Client()
    existing = next((d for d in client.list_datasets(dataset_name=dataset_name)), None)
    if existing:
        print(
            f"Dataset {dataset_name!r} already exists ({existing.id}); aborting.", file=sys.stderr
        )
        print("Delete it in the LangSmith UI or pass --dataset-name <new>.", file=sys.stderr)
        sys.exit(1)
    ds = client.create_dataset(
        dataset_name=dataset_name,
        description="Open SWE Reviewer baseline — 50 PRs from withmartian/code-review-benchmark goldens.",
    )
    client.create_examples(
        dataset_id=ds.id,
        inputs=[e["inputs"] for e in examples],
        outputs=[e["outputs"] for e in examples],
        metadata=[e["metadata"] for e in examples],
    )
    print(f"Uploaded {len(examples)} examples to {dataset_name} ({ds.id}).")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset-name", default="openswe-reviewer-v1")
    ap.add_argument("--dry-run", action="store_true", help="Build examples but don't upload.")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    raw = load_goldens()
    if args.limit:
        raw = raw[: args.limit]
    print(f"Loaded {len(raw)} golden entries; resolving SHAs via gh...")

    examples: list[dict] = []
    for i, entry in enumerate(raw, 1):
        try:
            ex = build_example(entry)
            examples.append(ex)
            print(f"  [{i}/{len(raw)}] {ex['inputs']['repo']}#{ex['inputs']['pr_number']} ok")
        except Exception as exc:
            print(f"  [{i}/{len(raw)}] FAILED for {entry.get('url')}: {exc}", file=sys.stderr)

    if args.dry_run:
        out = Path(__file__).parent / "dataset_dryrun.json"
        with out.open("w") as f:
            json.dump(examples, f, indent=2)
        print(f"Dry run: wrote {len(examples)} examples to {out}")
        return

    upload(args.dataset_name, examples)


if __name__ == "__main__":
    main()
