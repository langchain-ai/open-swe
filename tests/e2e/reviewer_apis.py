"""The fake GitHub review APIs, plus the control endpoints that drive them.

The reviewer graph publishes through six GitHub surfaces the PR-opening flow
never touches: the raw PR diff, review submission, inline review comments and
their replies, issue comments (its status comment), check runs, and the GraphQL
review-thread query and resolve mutation. All of them land here, and
``fakes.review_state`` is what the specs assert on.

Registered onto the harness app by :func:`register`, so ``harness.py`` stays the
Slack/PR-flow surface it already was.
"""

import base64
import hashlib
import hmac
import json
import os
import subprocess
from typing import Any

import fakes
import httpx
from e2e_env import BASE_URL, OWNER, REPO
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

_REVIEW_EVENTS = {"COMMENT", "APPROVE", "REQUEST_CHANGES", "PENDING"}


def _pr_or_404(owner: str, repo: str, number: int) -> dict[str, Any] | None:
    return fakes.find_pull(number, owner, repo)


def _not_found() -> JSONResponse:
    return JSONResponse({"message": "Not Found"}, status_code=404)


def register(app: FastAPI, *, webhook_secret: str, pr_json: Any) -> None:
    """Add the review APIs and their control endpoints to ``app``."""

    # --- the PR diff the reviewer anchors findings against -----------------
    @app.get("/fake-gh/repos/{owner}/{repo}/pulls/{number}/diff-passthrough")
    async def gh_pull_diff(owner: str, repo: str, number: int) -> Response:
        """Explicit diff route; the media-type path goes through the PR route."""
        pr = _pr_or_404(owner, repo, number)
        if pr is None:
            return _not_found()
        return PlainTextResponse(fakes.pull_diff(owner, repo, pr["base"], pr["head"]))

    # --- review submission -------------------------------------------------
    @app.post("/fake-gh/repos/{owner}/{repo}/pulls/{number}/reviews")
    async def gh_create_review(
        owner: str, repo: str, number: int, request: Request
    ) -> JSONResponse:
        pr = _pr_or_404(owner, repo, number)
        if pr is None:
            return _not_found()
        body = await request.json()
        event = str(body.get("event", "COMMENT")).upper()
        if event not in _REVIEW_EVENTS:
            return JSONResponse({"message": "Invalid event"}, status_code=422)
        comments = body.get("comments")
        comments = comments if isinstance(comments, list) else []
        diff_paths = {file["filename"] for file in pr["files"]}
        unanchored = [
            comment
            for comment in comments
            if isinstance(comment, dict) and comment.get("path") not in diff_paths
        ]
        if unanchored:
            # GitHub's real failure mode for an out-of-diff anchor, which the
            # publish path has a dedicated retry for.
            return JSONResponse(
                {
                    "message": "Validation Failed",
                    "errors": [
                        {"resource": "PullRequestReviewComment", "field": "path", "code": "invalid"}
                    ],
                },
                status_code=422,
            )
        review = fakes.add_review(
            owner,
            repo,
            number,
            body=str(body.get("body", "")),
            event=event,
            comments=[comment for comment in comments if isinstance(comment, dict)],
        )
        return JSONResponse(
            {
                "id": review["id"],
                "state": review["state"],
                "body": review["body"],
                "html_url": f"{BASE_URL}/mock/github/{owner}/{repo}/pull/{number}#review-{review['id']}",
                "user": {"login": review["author"]},
            },
            status_code=200,
        )

    @app.get("/fake-gh/repos/{owner}/{repo}/pulls/{number}/reviews")
    async def gh_list_reviews(owner: str, repo: str, number: int) -> JSONResponse:
        return JSONResponse(
            [
                {
                    "id": review["id"],
                    "state": review["state"],
                    "body": review["body"],
                    "user": {"login": review["author"]},
                }
                for review in fakes.reviews_for(owner, repo, number)
            ]
        )

    # --- inline review comments -------------------------------------------
    def _comment_json(comment: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": comment["id"],
            "node_id": comment["node_id"],
            "path": comment["path"],
            "line": comment["line"],
            "start_line": comment["start_line"],
            "side": comment["side"],
            "body": comment["body"],
            "in_reply_to_id": comment["in_reply_to_id"],
            "pull_request_review_id": comment["review_id"],
            "user": {"login": comment["author"]},
            "html_url": (
                f"{BASE_URL}/mock/github/{comment['owner']}/{comment['repo']}"
                f"/pull/{comment['pull_number']}#comment-{comment['id']}"
            ),
        }

    @app.get("/fake-gh/repos/{owner}/{repo}/pulls/{number}/comments")
    async def gh_list_review_comments(owner: str, repo: str, number: int) -> JSONResponse:
        return JSONResponse(
            [_comment_json(comment) for comment in fakes.review_comments_for(owner, repo, number)]
        )

    @app.post("/fake-gh/repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies")
    async def gh_reply_to_review_comment(
        owner: str,  # noqa: ARG001
        repo: str,  # noqa: ARG001
        number: int,  # noqa: ARG001
        comment_id: int,
        request: Request,
    ) -> JSONResponse:
        body = await request.json()
        reply = fakes.reply_to_review_comment(comment_id, str(body.get("body", "")))
        if reply is None:
            return _not_found()
        return JSONResponse(_comment_json(reply), status_code=201)

    # --- issue comments (the reviewer's status comment) --------------------
    @app.get("/fake-gh/repos/{owner}/{repo}/issues/{number}/comments")
    async def gh_list_issue_comments(owner: str, repo: str, number: int) -> JSONResponse:
        return JSONResponse(
            [
                {"id": comment["id"], "body": comment["body"], "user": {"login": comment["author"]}}
                for comment in fakes.issue_comments_for(owner, repo, number)
            ]
        )

    @app.post("/fake-gh/repos/{owner}/{repo}/issues/{number}/comments")
    async def gh_create_issue_comment(
        owner: str, repo: str, number: int, request: Request
    ) -> JSONResponse:
        body = await request.json()
        comment = fakes.add_issue_comment(owner, repo, number, str(body.get("body", "")))
        return JSONResponse(
            {
                "id": comment["id"],
                "body": comment["body"],
                "user": {"login": comment["author"]},
                "html_url": (
                    f"{BASE_URL}/mock/github/{owner}/{repo}/pull/{number}#issuecomment-{comment['id']}"
                ),
            },
            status_code=201,
        )

    @app.patch("/fake-gh/repos/{owner}/{repo}/issues/comments/{comment_id}")
    async def gh_update_issue_comment(
        owner: str,  # noqa: ARG001
        repo: str,  # noqa: ARG001
        comment_id: int,
        request: Request,
    ) -> JSONResponse:
        body = await request.json()
        comment = fakes.update_issue_comment(comment_id, str(body.get("body", "")))
        if comment is None:
            return _not_found()
        return JSONResponse({"id": comment["id"], "body": comment["body"]})

    @app.delete("/fake-gh/repos/{owner}/{repo}/issues/comments/{comment_id}")
    async def gh_delete_issue_comment(
        owner: str,  # noqa: ARG001
        repo: str,  # noqa: ARG001
        comment_id: int,
    ) -> Response:
        if not fakes.delete_issue_comment(comment_id):
            return _not_found()
        return Response(status_code=204)

    # --- check runs (the `Open SWE Review` check) --------------------------
    @app.post("/fake-gh/repos/{owner}/{repo}/check-runs")
    async def gh_create_check_run(owner: str, repo: str, request: Request) -> JSONResponse:
        check_run = fakes.create_check_run(owner, repo, await request.json())
        return JSONResponse({"id": check_run["id"], "name": check_run["name"]}, status_code=201)

    @app.patch("/fake-gh/repos/{owner}/{repo}/check-runs/{check_run_id}")
    async def gh_update_check_run(
        owner: str,  # noqa: ARG001
        repo: str,  # noqa: ARG001
        check_run_id: int,
        request: Request,
    ) -> JSONResponse:
        check_run = fakes.update_check_run(check_run_id, await request.json())
        if check_run is None:
            return _not_found()
        return JSONResponse({"id": check_run["id"], "conclusion": check_run["conclusion"]})

    # --- repository contents (AGENTS.md lookups during review) ------------
    @app.get("/fake-gh/repos/{owner}/{repo}/contents/{path:path}")
    async def gh_get_contents(owner: str, repo: str, path: str, ref: str = "") -> JSONResponse:
        try:
            content = fakes.file_at_ref(owner, repo, ref or "HEAD", path)
        except subprocess.CalledProcessError:
            return _not_found()
        if content is None:
            return _not_found()
        return JSONResponse(
            {
                "name": path.rsplit("/", 1)[-1],
                "path": path,
                "encoding": "base64",
                "content": base64.b64encode(content).decode(),
            }
        )

    # --- control endpoints the specs drive --------------------------------
    @app.get("/control/review-state")
    async def control_review_state(
        number: int, owner: str = OWNER, repo: str = REPO
    ) -> JSONResponse:
        """Everything the reviewer published on a PR — empty when it stood down."""
        return JSONResponse(fakes.review_state(owner, repo, number))

    @app.post("/control/open-pull-request")
    async def control_open_pull_request(request: Request) -> JSONResponse:
        """Create a PR the agent did not open, so it carries no inline-review claim."""
        body = await request.json()
        pr = fakes.create_pull(
            str(body.get("owner", OWNER)),
            str(body.get("repo", REPO)),
            head=str(body.get("head", "")),
            base=str(body.get("base", "main")),
            title=str(body.get("title", "")),
            body=str(body.get("body", "")),
            draft=bool(body.get("draft", False)),
        )
        return JSONResponse(pr_json(pr), status_code=201)

    @app.post("/control/review-repo-enabled")
    async def control_review_repo_enabled(request: Request) -> JSONResponse:
        """Opt the demo repo into automatic review (a real dashboard setting)."""
        from agent.dashboard.enabled_repos import set_review_repo_enabled

        body = await request.json()
        enabled = bool(body.get("enabled", True))
        full_name = str(body.get("full_name", f"{OWNER}/{REPO}"))
        return JSONResponse({"enabled_repos": await set_review_repo_enabled(full_name, enabled)})

    @app.post("/control/forget-review-state")
    async def control_forget_review_state(request: Request) -> JSONResponse:
        """Drop the durable review state for a PR number: its reviewer thread and
        its inline-review claim.

        ``/control/reset`` clears the fake GitHub, but findings and claims live in
        the LangGraph store, which outlives the process. PR numbers restart at 1
        every reset, so without this a rerun inherits the previous run's
        published findings and ``publish_review`` correctly skips as a duplicate.
        """
        from langgraph_sdk import get_client as get_langgraph_client

        from agent.review.inline_review import REVIEWS as INLINE_REVIEWS
        from agent.review.inline_review import review_key
        from agent.thread_ids import reviewer_thread_id

        body = await request.json()
        numbers = body.get("pr_numbers")
        numbers = numbers if isinstance(numbers, list) else [1, 2]
        owner = str(body.get("owner", OWNER))
        repo = str(body.get("repo", REPO))
        client = get_langgraph_client(url=os.environ["LANGGRAPH_URL"])
        forgotten: list[int] = []
        for raw in numbers:
            number = int(raw)
            try:
                await client.threads.delete(reviewer_thread_id(owner, repo, number))
            except Exception:  # noqa: BLE001, S110
                pass
            await INLINE_REVIEWS.delete(review_key(owner, repo, number))
            forgotten.append(number)
        return JSONResponse({"forgotten": forgotten})

    @app.post("/control/github-webhook")
    async def control_github_webhook(request: Request) -> JSONResponse:
        """Deliver a signed GitHub webhook to the real ``/webhooks/github`` route.

        ``event`` names the X-GitHub-Event; ``pr_number`` builds a
        ``pull_request`` payload from the fake store so a spec does not have to
        hand-write one.
        """
        body = await request.json()
        event = str(body.get("event", "pull_request"))
        payload = body.get("payload")
        if not isinstance(payload, dict):
            pr = fakes.find_pull(
                int(body.get("pr_number", 0)),
                str(body.get("owner", OWNER)),
                str(body.get("repo", REPO)),
            )
            if pr is None:
                return JSONResponse({"error": "unknown pr"}, status_code=404)
            payload = _pull_request_payload(pr, action=str(body.get("action", "opened")))
        response = await _deliver_github_webhook(app, event, payload, secret=webhook_secret)
        return JSONResponse(
            {
                "status": response.status_code,
                "body": _json_or_text(response),
            }
        )


def _json_or_text(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _pull_request_payload(pr: dict[str, Any], *, action: str) -> dict[str, Any]:
    return {
        "action": action,
        "repository": {
            "owner": {"login": pr["owner"]},
            "name": pr["repo"],
            "id": 1,
            "private": fakes.repo_private(),
        },
        "pull_request": {
            "number": pr["number"],
            "html_url": f"{BASE_URL}/mock/github/{pr['owner']}/{pr['repo']}/pull/{pr['number']}",
            "title": pr["title"],
            "draft": pr["draft"],
            "user": {"login": pr["author"], "id": 2},
            "head": {"ref": pr["head"], "sha": pr["head_sha"]},
            "base": {"ref": pr["base"], "sha": pr["base_sha"]},
        },
        "sender": {"login": pr["author"], "id": 2},
    }


async def _deliver_github_webhook(
    app: FastAPI, event: str, payload: dict[str, Any], *, secret: str
) -> httpx.Response:
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://harness") as client:
        return await client.post(
            "/webhooks/github",
            content=raw,
            headers={
                "X-GitHub-Event": event,
                "X-Hub-Signature-256": signature,
                "X-GitHub-Delivery": f"e2e-{event}-{payload.get('action', '')}",
                "Content-Type": "application/json",
            },
        )
