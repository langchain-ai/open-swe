"""Server-owned publication; write credentials never enter the docs sandbox."""

import base64
import shlex
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from deepagents.backends.protocol import SandboxBackendProtocol
from pydantic import BaseModel, Field, TypeAdapter

from agent.docs import github
from agent.docs.collect_changes import validate_path
from agent.docs.coordinator import complete_check, job_lock, publication_guard
from agent.docs.models import JOBS, DocsSnapshot, Repository
from agent.github.app import PermissionMap

DOCS_WRITE: PermissionMap = {"contents": "write", "pull_requests": "write"}
COMMENT_WRITE: PermissionMap = {"issues": "write"}


class Change(BaseModel):
    path: str
    content: str | None


class SHA(BaseModel):
    sha: str


class GitCommit(SHA):
    tree: SHA


class CreatedPR(BaseModel):
    html_url: str
    number: int
    state: str


class Finding(BaseModel):
    docs_pr_number: int = Field(gt=0)
    description: str = Field(min_length=1, max_length=12000)


def protect_private_source(snapshot: DocsSnapshot, text: str) -> None:
    if snapshot.source.base.repo is None or snapshot.source.base.repo.private:
        identifiers = (
            snapshot.source.html_url,
            snapshot.source_repository,
            snapshot.source.head.sha,
            snapshot.source_repository.split("/")[1] + "/",
        )
        if any(identifier.lower() in text.lower() for identifier in identifiers):
            raise ValueError("Public docs output must not identify a private source repository")


async def comment_once(repository: str, number: int, marker: str, body: str) -> None:
    if any(marker in (comment.body or "") for comment in await github.comments(repository, number)):
        return
    await github.request(
        repository,
        f"issues/{number}/comments",
        method="POST",
        data={"body": f"{body}\n\n{marker}"},
        permissions=COMMENT_WRITE,
    )


async def collect_changes(backend: SandboxBackendProtocol, root: str, base: str) -> list[Change]:
    script = Path(__file__).with_name("collect_changes.py").read_text()
    destination = "/tmp/open-swe-docs-changes.json"
    command = (
        "python3 -c "
        + shlex.quote(script)
        + " "
        + " ".join(map(shlex.quote, (root, base, destination)))
    )
    result = await backend.aexecute(command)
    if result.exit_code != 0:
        raise ValueError("Could not collect safe docs changes: " + result.output[-2000:])
    downloaded = await backend.adownload_files([destination])
    if len(downloaded) != 1 or downloaded[0].content is None or downloaded[0].error:
        raise ValueError("Could not download docs changes")
    if len(downloaded[0].content) > 15 * 1024 * 1024:
        raise ValueError("Docs change manifest is too large")
    changes = TypeAdapter(list[Change]).validate_json(downloaded[0].content)
    if not changes or len(changes) > 100:
        raise ValueError("Publish requires 1–100 changed docs files")
    total = 0
    for change in changes:
        validate_path(change.path)
        if change.content is not None:
            decoded = base64.b64decode(change.content, validate=True)
            if len(decoded) > 2 * 1024 * 1024:
                raise ValueError("Docs file exceeds 2 MiB")
            total += len(decoded)
    if total > 10 * 1024 * 1024:
        raise ValueError("Docs PR exceeds 10 MiB")
    return changes


async def publish(
    snapshot: DocsSnapshot,
    backend: SandboxBackendProtocol,
    docs_root: str,
    title: str,
    body: str,
) -> str:
    async with job_lock(snapshot.key):
        job = await publication_guard(snapshot)
        if snapshot.links:
            raise ValueError("Linked docs PRs are review-only; post findings instead")
        repository = snapshot.settings.docs_repository
        repo = Repository.model_validate(await github.request(repository, ""))
        if not repo.private:
            protect_private_source(snapshot, title + "\n" + body)
        branch = f"open-swe/docs/{uuid5(NAMESPACE_URL, snapshot.key).hex[:16]}/{snapshot.source.head.sha[:12]}"
        owner = repository.split("/")[0]
        # Covers retries after PR creation, including a failure before persisting the URL.
        existing = TypeAdapter(list[CreatedPR]).validate_python(
            await github.request(
                repository,
                f"pulls?state=all&head={github.quote(owner + ':' + branch, safe='')}&per_page=100",
            )
        )
        if existing:
            url = existing[0].html_url
        else:
            changes = await collect_changes(backend, docs_root, snapshot.docs_base_sha)
            tree_entries: list[dict[str, object]] = []
            for change in changes:
                entry: dict[str, object] = {
                    "path": change.path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": None,
                }
                if change.content is not None:
                    if not repo.private:
                        protect_private_source(
                            snapshot,
                            base64.b64decode(change.content).decode("utf-8", errors="ignore"),
                        )
                    blob = SHA.model_validate(
                        await github.request(
                            repository,
                            "git/blobs",
                            method="POST",
                            data={"content": change.content, "encoding": "base64"},
                            permissions=DOCS_WRITE,
                        )
                    )
                    entry["sha"] = blob.sha
                tree_entries.append(entry)
            base = GitCommit.model_validate(
                await github.request(repository, "git/commits/" + snapshot.docs_base_sha)
            )
            tree = SHA.model_validate(
                await github.request(
                    repository,
                    "git/trees",
                    method="POST",
                    data={"base_tree": base.tree.sha, "tree": tree_entries},
                    permissions=DOCS_WRITE,
                )
            )
            commit = SHA.model_validate(
                await github.request(
                    repository,
                    "git/commits",
                    method="POST",
                    data={
                        "message": title[:200],
                        "tree": tree.sha,
                        "parents": [snapshot.docs_base_sha],
                    },
                    permissions=DOCS_WRITE,
                )
            )
            # Check again after uploads. The operation never updates an existing PR branch.
            await publication_guard(snapshot)

            class Ref(BaseModel):
                object: SHA

            async with github.github_client(
                token=await github.token(repository, DOCS_WRITE)
            ) as client:
                url_ref = f"{github.GITHUB_API_BASE}/repos/{repository}/git/ref/heads/{branch}"
                response = await github.github_request(client, "GET", url_ref)
                if response.status_code == 404:
                    await github.request(
                        repository,
                        "git/refs",
                        method="POST",
                        data={"ref": "refs/heads/" + branch, "sha": commit.sha},
                        permissions=DOCS_WRITE,
                    )
                else:
                    response.raise_for_status()
                    ref = Ref.model_validate(response.json())
                    previous_commit = GitCommit.model_validate(
                        await github.request(repository, "git/commits/" + ref.object.sha)
                    )
                    if previous_commit.tree.sha != tree.sha:
                        raise ValueError(
                            "Docs branch already exists with different changes; refusing to overwrite"
                        )
            await publication_guard(snapshot)
            source_line = ""
            if snapshot.source.base.repo is not None and not snapshot.source.base.repo.private:
                source_line = f"\n\nSource PR: {snapshot.source.html_url}"
            created = CreatedPR.model_validate(
                await github.request(
                    repository,
                    "pulls",
                    method="POST",
                    data={
                        "head": branch,
                        "base": snapshot.settings.docs_base_branch,
                        "title": title[:200],
                        "body": body
                        + source_line
                        + "\n\nAuthored by Open SWE Docs; review before merging.",
                        "draft": True,
                    },
                    permissions=DOCS_WRITE,
                )
            )
            url = created.html_url
        job.docs_pr_url = url
        # Save first: a failed source comment cannot orphan the created docs PR.
        await JOBS.put(snapshot.key, job)
        await publication_guard(snapshot)
        marker = f"<!-- open-swe-docs:{snapshot.fingerprint}:created -->"
        await comment_once(
            snapshot.source_repository,
            snapshot.source.number,
            marker,
            f"Open SWE Docs created a documentation PR: {url}\n\nPlease review it alongside this change.",
        )
        job.status = "completed"
        job.result = f"Documentation PR: {url}"
        await JOBS.put(snapshot.key, job)
        await complete_check(job, "Documentation PR created", job.result)
        return url


async def finish(
    snapshot: DocsSnapshot, summary: str, findings: list[Finding], docs_needed: bool
) -> str:
    async with job_lock(snapshot.key):
        job = await publication_guard(snapshot)
        if docs_needed and not snapshot.links:
            raise ValueError("Use publish_docs_pr when documentation is needed and no PR is linked")
        numbers = {link.number for link in snapshot.links}
        if any(finding.docs_pr_number not in numbers for finding in findings):
            raise ValueError("Findings must refer to a validated linked docs PR")
        if docs_needed and not findings:
            raise ValueError("Describe the required corrections for each affected linked docs PR")
        if findings and not docs_needed:
            raise ValueError("Set docs_needed=true when reporting required corrections")
        if findings:
            docs_repo = Repository.model_validate(
                await github.request(snapshot.settings.docs_repository, "")
            )
            for number in sorted({finding.docs_pr_number for finding in findings}):
                descriptions = [
                    finding.description for finding in findings if finding.docs_pr_number == number
                ]
                body = "Open SWE Docs found documentation changes needed:\n\n" + "\n\n".join(
                    descriptions
                )
                if not docs_repo.private:
                    protect_private_source(snapshot, body)
                await publication_guard(snapshot)
                await comment_once(
                    snapshot.settings.docs_repository,
                    number,
                    f"<!-- open-swe-docs:{snapshot.fingerprint}:{number} -->",
                    body,
                )
            await publication_guard(snapshot)
            await comment_once(
                snapshot.source_repository,
                snapshot.source.number,
                f"<!-- open-swe-docs:{snapshot.fingerprint}:review -->",
                "Open SWE Docs found corrections needed in the linked docs PR(s):\n\n"
                + "\n".join(
                    link.url
                    for link in snapshot.links
                    if any(f.docs_pr_number == link.number for f in findings)
                ),
            )
        if job.docs_pr_url and not any(
            job.docs_pr_url in (comment.body or "")
            for comment in await github.comments(snapshot.source_repository, snapshot.source.number)
        ):
            await publication_guard(snapshot)
            await comment_once(
                snapshot.source_repository,
                snapshot.source.number,
                f"<!-- open-swe-docs:{snapshot.fingerprint}:recovered -->",
                f"Documentation PR for this change: {job.docs_pr_url}",
            )
        job.status = "completed"
        job.result = summary[:12000]
        await JOBS.put(snapshot.key, job)
        check_summary = (
            f"Corrections were reported on {len({finding.docs_pr_number for finding in findings})} linked docs PR(s)."
            if findings
            else "The documentation assessment is complete."
        )
        # Checks can be public while the docs repository and detailed assessment are private.
        await complete_check(job, "Docs review complete", check_summary)
        return "Docs review completed."
