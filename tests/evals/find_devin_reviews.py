"""
Finds closed PRs from langchainplus repos that have Devin reviews.

Usage:
    GH_TOKEN=<your_pat> python evals/find_devin_reviews.py
"""

import json
import os
import sys
import time

import requests

GH_TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
if not GH_TOKEN:
    print("ERROR: Set GH_TOKEN env var with a GitHub PAT (repo scope)")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

ORG = "langchainplus"
DEVIN_LOGIN = "devin-ai-integration[bot]"


def get(url, params=None):
    r = requests.get(url, headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()


def list_org_repos():
    repos, page = [], 1
    while True:
        data = get(f"https://api.github.com/orgs/{ORG}/repos", params={"per_page": 100, "page": page, "type": "all"})
        if not data:
            break
        repos.extend(data)
        if len(data) < 100:
            break
        page += 1
    return repos


def list_closed_prs(repo, max_prs=200):
    prs, page = [], 1
    while len(prs) < max_prs:
        data = get(
            f"https://api.github.com/repos/{ORG}/{repo}/pulls",
            params={"state": "closed", "per_page": 100, "page": page, "sort": "updated", "direction": "desc"},
        )
        if not data:
            break
        prs.extend(data)
        if len(data) < 100:
            break
        page += 1
        time.sleep(0.2)
    return prs[:max_prs]


def get_pr_reviews(repo, pr_number):
    return get(f"https://api.github.com/repos/{ORG}/{repo}/pulls/{pr_number}/reviews")


def get_pr_review_comments(repo, pr_number):
    return get(f"https://api.github.com/repos/{ORG}/{repo}/pulls/{pr_number}/comments")


def main():
    print(f"Fetching repos from org: {ORG}\n")
    repos = list_org_repos()
    print(f"Found {len(repos)} repos\n")

    results = []

    for repo_obj in repos:
        repo = repo_obj["name"]
        print(f"Checking {ORG}/{repo} ...")
        try:
            prs = list_closed_prs(repo)
        except requests.HTTPError as e:
            print(f"  Skipping ({e})")
            continue

        for pr in prs:
            pr_number = pr["number"]
            try:
                reviews = get_pr_reviews(repo, pr_number)
            except requests.HTTPError:
                continue

            devin_reviews = [r for r in reviews if r["user"]["login"] == DEVIN_LOGIN]
            if not devin_reviews:
                continue

            try:
                all_inline = get_pr_review_comments(repo, pr_number)
            except requests.HTTPError:
                all_inline = []

            for review in devin_reviews:
                inline = [
                    {"file": c["path"], "line": c.get("line") or c.get("original_line"), "body": c["body"]}
                    for c in all_inline
                    if c["user"]["login"] == DEVIN_LOGIN and c.get("pull_request_review_id") == review["id"]
                ]
                entry = {
                    "pr_number": pr_number,
                    "title": pr["title"],
                    "url": pr["html_url"],
                    "repo": f"{ORG}/{repo}",
                    "pr_outcome": "merged" if pr["merged_at"] else "closed",
                    "pr_merged_at": pr["merged_at"],
                    "commit_id": review["commit_id"],
                    "review_state": review["state"],
                    "review_body": review["body"],
                    "inline_comments": inline,
                }
                results.append(entry)
                print(f"  [FOUND] PR #{pr_number} | {review['state']} | outcome={entry['pr_outcome']} | {pr['title'][:60]}")

            time.sleep(0.3)

    out = "evals/devin_reviews_raw.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nTotal Devin reviews found: {len(results)}")
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
