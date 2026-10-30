"""The thread's sandbox as both sides ready it: where its checkouts live and who commits in them."""

from typing import Final

# Repositories live in ``/workspace/<repo>``, where workspace images preload them.
WORK_DIR: Final = "/workspace"
GIT_NAME: Final = "open-swe[bot]"
# The open-swe user's noreply address: the bot's numeric noreply
# (215916821+open-swe[bot]@...) doesn't resolve to a GitHub account Vercel accepts,
# which broke preview deploys on commits carrying this co-author.
GIT_EMAIL: Final = "open-swe@users.noreply.github.com"
