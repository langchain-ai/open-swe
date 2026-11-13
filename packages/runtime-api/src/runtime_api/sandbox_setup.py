"""The thread's sandbox as both sides ready it: where its checkouts live and who commits in them."""

from typing import Final

from pydantic import BaseModel

# Repositories live in ``/workspace/<repo>``, where workspace images preload them.
WORK_DIR: Final = "/workspace"
GIT_NAME: Final = "Robin"
# The open-swe user's noreply address: the bot's numeric noreply
# (215916821+open-swe[bot]@...) doesn't resolve to a GitHub account Vercel accepts,
# which broke preview deploys on commits carrying this co-author.
GIT_EMAIL: Final = "open-swe@users.noreply.github.com"


class GitIdentity(BaseModel):
    """Who commits in the thread's sandbox: its owner, or the bot when it has none."""

    name: str = GIT_NAME
    email: str = GIT_EMAIL
