"""Seed the global Context Hub skills repo with the bundled SKILL.md playbooks.

Run once to populate ``OPENSWE_SKILLS_REPO`` (default ``-/openswe-skills``) so
the analyzer and main agent source skills from the hub instead of the bundled
copy. Idempotent: re-running pushes a new commit with the current bundled
content.

Usage:
    uv run python -m scripts.seed_skills_hub [--repo -/openswe-skills] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys

from langsmith import Client
from langsmith.schemas import FileEntry

from agent.utils.skills_hub import DEFAULT_GLOBAL_SKILLS_REPO, bundled_skill_files_for_hub

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        default=DEFAULT_GLOBAL_SKILLS_REPO,
        help="Hub agent repo to seed (owner/name or -/name).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the files that would be pushed without committing.",
    )
    args = parser.parse_args()

    files = bundled_skill_files_for_hub()
    for path in sorted(files):
        logger.info("  %s (%d bytes)", path, len(files[path]))

    if args.dry_run:
        logger.info("Dry run: would push %d file(s) to %s", len(files), args.repo)
        return 0

    payload = {path: FileEntry(type="file", content=content) for path, content in files.items()}
    url = Client().push_agent(args.repo, files=payload)
    logger.info("Pushed %d skill file(s) to %s -> %s", len(files), args.repo, url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
