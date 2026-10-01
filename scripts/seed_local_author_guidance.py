"""Seed one PR's human turns into a local thread, so the review scout has human input to read.

A local checkout has never run an agent, so no thread carries the messages
:class:`SteeringHistory` looks for and the human input card can never populate.
This writes them straight into a thread's checkpoint through the public SDK,
wrapped in the same ``<input-message>`` envelope a dashboard message carries,
and links that thread to a real pull request.

Summarising is not done here — the review scout does that through
``record_human_input``.

    uv run --env-file .env python scripts/seed_local_author_guidance.py \
        <owner> <repo> <number> <turns.json> [langgraph_url]

``turns.json`` is a list of the messages a person typed, oldest first.
"""

import asyncio
import json
import subprocess
import sys

from langgraph_sdk import get_client

from agent.github.pull_requests import PullRequest
from agent.input_messages import human_input
from agent.review.author_guidance import SteeringHistory
from agent.thread_ids import pr_comment_thread_id


def github_login() -> str:
    result = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "local-dev"


async def main() -> int:
    owner, repo, number, turns_path = sys.argv[1:5]
    url = sys.argv[5] if len(sys.argv) > 5 else "http://127.0.0.1:2024"
    turns = json.loads(open(turns_path, encoding="utf-8").read())
    login = github_login()
    thread_id = pr_comment_thread_id(owner, repo, int(number))

    client = get_client(url=url)
    # ``messages`` appends, and the server will not coerce a RemoveMessage sent
    # as state, so the only way to re-seed without stacking another copy of the
    # conversation on the last one is to start the thread over. Safe here: the
    # id is derived from the PR and this thread only ever holds seed data.
    await client.threads.delete(thread_id)
    await client.threads.create(thread_id=thread_id, if_exists="do_nothing")
    # ``graph_id`` is normally set by the thread's first run, and update_state
    # refuses to write a checkpoint without one.
    await client.threads.update(thread_id, metadata={"graph_id": "agent", "source": "web"})
    await client.threads.update_state(
        thread_id,
        {
            "messages": [
                human_input(
                    body,
                    {"sender_id": f"github:{login}", "surface": "web", "kind": "human"},
                )
                for body in turns
            ]
        },
        as_node="__start__",
    )

    pull_request = await PullRequest(owner=owner, repo=repo, number=int(number)).save()
    await pull_request.link_thread(thread_id, source="local-seed")

    history = await SteeringHistory.load(owner, repo, int(number))
    if history is None:
        print("no steering history read back")
        return 1
    print(f"seeded {len(history.follow_ups)} follow-up(s) as {login} on {owner}/{repo}#{number}")
    print(f"request: {history.request.text[:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
