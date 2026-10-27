"""Reflow Markdown and Markdown Jinja files to one sentence per line with rumdl.

In a Jinja template rumdl would join template tags, placeholders, "Key: {{ value }}"
lines and a paragraph's title line into the prose around them, which changes what the
template renders. Those lines are swapped for HTML comments, which rumdl leaves alone,
for the length of the run.
"""

import re
import subprocess
import sys
from pathlib import Path

KEY_VALUE = re.compile(r"^[A-Za-z][\w .`/-]{0,30}: ")
TERMINATED = (".", "!", "?", ":", ";", ",", ")", "]", "`", ">", "*", "_", '"', "'")
PLACEHOLDER = re.compile(r"^<!-- jinja:(\d+) -->$")


def is_template_line(line: str) -> bool:
    """A line whose own shape carries meaning, so it must stay on its own line."""
    text = line.strip()
    if text.startswith(("{{", "{%", "{#")):
        return True
    return "{{" in text and bool(KEY_VALUE.match(text))


def protect(lines: list[str]) -> tuple[list[str], list[str]]:
    kept: list[str] = []
    out: list[str] = []
    in_tag = False
    fence = False
    paragraph_start = True
    for index, line in enumerate(lines):
        text = line.strip()
        if text.startswith("```"):
            fence = not fence
        keep = in_tag or is_template_line(line)
        if in_tag and ("%}" in line or "#}" in line):
            in_tag = False
        elif not in_tag and text.startswith(("{%", "{#")) and "%}" not in line and "#}" not in line:
            in_tag = True
        following = lines[index + 1].strip() if index + 1 < len(lines) else ""
        is_title = (
            paragraph_start
            and not fence
            and bool(following)
            and not text.startswith(("#", "-", "*", ">", "<", "|"))
            and 0 < len(text) < 90
            and not text.endswith(TERMINATED)
        )
        if keep or is_title:
            out.append(f"<!-- jinja:{len(kept)} -->")
            kept.append(line)
        else:
            out.append(line)
        paragraph_start = text == ""
    return out, kept


def restore(lines: list[str], kept: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        match = PLACEHOLDER.match(line)
        out.append(kept[int(match.group(1))] if match else line)
    return out


def reflow(path: Path) -> None:
    original = path.read_text()
    jinja = path.suffix == ".jinja"
    protected, kept = protect(original.splitlines()) if jinja else (original.splitlines(), [])
    path.write_text("\n".join(protected) + "\n")
    subprocess.run(["rumdl", "check", "--fix", "--quiet", str(path)], check=False)
    reflowed = restore(path.read_text().splitlines(), kept)
    path.write_text("\n".join(reflowed) + "\n")


def main(paths: list[str]) -> int:
    changed = 0
    for name in paths:
        path = Path(name)
        before = path.read_text()
        reflow(path)
        changed += path.read_text() != before
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
