"""Connect the review page to the pull request's main agent thread."""

from typing import TypedDict

from openswe.threads import pr_fixes


class ReviewChat(TypedDict):
    available: bool
    assistant_id: str
    thread_id: str


async def get_review_chat(
    owner: str, repo: str, pr_number: int, login: str, email: str | None = None
) -> ReviewChat:
    thread = await pr_fixes.start_pull_request_thread(
        owner,
        repo,
        pr_number,
        login,
        email,
        intent=pr_fixes.OpenThreadIntent(
            intent="open", title=f"Discuss {owner}/{repo}#{pr_number}"
        ),
    )
    return {"available": True, "assistant_id": "agent", "thread_id": thread.thread_id}
