import re
import sys
from collections.abc import Iterable

REFERENCE = re.compile(
    r"\b(?:(?:get_)?langgraph_client|get_(?:sync_)?client|(?:Sync)?LangGraphClient|langgraph_sdk\.client)\b"
)
REASON = "No new backend langgraph_client uses: everything must be on MDA now."


def violations(diff: Iterable[str]) -> list[str]:
    path = ""
    line_number = 0
    errors: list[str] = []
    for line in diff:
        if line.startswith("+++ b/"):
            path = line[6:].strip()
        elif line.startswith("@@ "):
            line_number = int(line.split(" +", 1)[1].split(",", 1)[0].split(" ", 1)[0])
        elif line.startswith("+"):
            if REFERENCE.search(line[1:]):
                errors.append(f"{path}:{line_number}: {REASON}")
            line_number += 1
        elif line.startswith(" "):
            line_number += 1
    return errors


if __name__ == "__main__":
    errors = violations(sys.stdin)
    for error in errors:
        print(error, file=sys.stderr)
    sys.exit(bool(errors))
