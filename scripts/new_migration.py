#!/usr/bin/env python3
"""Create the next migration: numbered filename, random revision ID chained to every head."""

import re
import sys

from alembic.script import ScriptDirectory
from alembic.util import rev_id

from agent.database.postgres import MIGRATION_DIR

message = " ".join(sys.argv[1:]).strip()
if not message:
    sys.exit('usage: make migration m="Short description"')

versions = MIGRATION_DIR / "versions"
number = 1 + max(
    int(match[1]) for path in versions.glob("*.py") if (match := re.match(r"(\d{4})_", path.name))
)
script = ScriptDirectory(str(MIGRATION_DIR), file_template=f"{number:04d}_%(rev)s_%(slug)s")
created = script.generate_revision(rev_id(), message, head="heads")
if created is None:
    sys.exit("alembic did not create a migration")
print(created.path)
