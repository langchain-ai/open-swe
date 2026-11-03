"""Runs inside a sandbox to do a walkthrough's line-level work next to the pull request's checkout.

The backend keeps a walkthrough as ranges of changed lines and never reads the
diff itself; everything that needs the diff's lines or text happens here. Run
by ``openswe.walkthrough.checkout`` through ``SandboxScript``. Each request
names an ``op``:

- ``index``: every changed file's git hunks, each as its blocks of changed lines.
- ``maps``: how lines moved between an earlier head and merge base and the
  pinned ones, so line sets can be carried to the new head.
- ``render``: line sets as a reader sees them, at real line numbers.
- ``wordless``: which line sets hold only braces, punctuation or blank lines.
"""

import base64
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys

HEAD_REF = "refs/walkthrough/head"
MERGE_BASE_REF = "refs/walkthrough/merge-base"
GIT = ["git", "-c", "core.quotePath=false"]
DIFF = [*GIT, "diff", "--no-color", "--no-ext-diff", "--no-renames", "--full-index"]
CONTEXT_LINES = 2
PATHS_PER_CALL = 500
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
WORD_RE = re.compile(r"\w")
LANGUAGES = {
    "py": "python",
    "ts": "typescript",
    "tsx": "typescript",
    "js": "javascript",
    "jsx": "javascript",
    "go": "go",
    "rs": "rust",
    "sql": "sql",
    "sh": "bash",
    "yml": "yaml",
    "yaml": "yaml",
    "json": "json",
    "md": "markdown",
    "css": "css",
    "html": "html",
}


class Refused(Exception):
    pass


def git_lines(args):
    """Lines of a git command's output, read as they come."""
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout is not None
    for raw in process.stdout:
        yield raw.decode("utf-8", errors="replace").rstrip("\n")
    error = process.stderr.read().decode(errors="replace") if process.stderr else ""
    if process.wait() != 0:
        raise Refused(error.strip()[-2000:] or "git failed")


def git_text(args):
    result = subprocess.run(args, capture_output=True)
    if result.returncode != 0:
        raise Refused(result.stderr.decode(errors="replace").strip()[-2000:] or "git failed")
    return result.stdout.decode("utf-8", errors="replace")


def parse_index(lines):
    """Each file's hunks from a diff's lines, every hunk as its blocks of changed lines.

    A block is ``[old_start, old_len, new_start, new_len]`` as ``git diff -U0``
    would head it: with a length of zero, the start is the line just before
    the change.
    """
    files = []
    current = None
    hunk = None
    block = None
    old = new = 0

    def close():
        if block is not None and hunk is not None:
            hunk.append(
                [
                    block[0] if block[1] else block[0] - 1,
                    block[1],
                    block[2] if block[3] else block[2] - 1,
                    block[3],
                ]
            )

    for line in lines:
        if line.startswith("diff --git "):
            close()
            block = hunk = None
            current = {"path": line.rpartition(" b/")[2], "header": [line], "hunks": []}
            files.append(current)
            continue
        if current is None:
            continue
        match = HUNK_RE.match(line)
        if match:
            close()
            block = None
            hunk = []
            current["hunks"].append(hunk)
            # A side of length zero is numbered from the line before it.
            old = int(match.group(1)) + (match.group(2) == "0")
            new = int(match.group(3)) + (match.group(4) == "0")
            continue
        if hunk is None:
            current["header"].append(line)
            if line.startswith("+++ b/"):
                current["path"] = line[6:]
            continue
        if line.startswith("-") or line.startswith("+"):
            if block is None:
                block = [old, 0, new, 0]
            if line.startswith("-"):
                block[1] += 1
                old += 1
            else:
                block[3] += 1
                new += 1
        elif not line.startswith("\\"):
            close()
            block = None
            old += 1
            new += 1
    close()
    return [
        {
            "path": found["path"],
            "hunks": found["hunks"],
            "textless": not found["hunks"],
            "deleted": any(line.startswith("deleted file mode") for line in found["header"]),
            "fingerprint": hashlib.sha256("\n".join(found["header"]).encode()).hexdigest(),
        }
        for found in files
    ]


def index(_request):
    return {"files": parse_index(git_lines([*DIFF, MERGE_BASE_REF, HEAD_REF]))}


def ensure_commit(sha):
    if subprocess.run([*GIT, "cat-file", "-e", sha + "^{commit}"], capture_output=True).returncode:
        subprocess.run([*GIT, "fetch", "--quiet", "origin", sha], capture_output=True)
    return (
        subprocess.run([*GIT, "cat-file", "-e", sha + "^{commit}"], capture_output=True).returncode
        == 0
    )


def line_map(old, new, paths):
    if old == new:
        return {}
    return {
        found["path"]: [block for hunk in found["hunks"] for block in hunk]
        for found in parse_index(git_lines([*DIFF, "-U0", old, new]))
        if found["path"] in paths
    }


def maps(request):
    old_head = request["old_head"]
    if not ensure_commit(old_head):
        return {"missing": old_head}
    old_base = request.get("old_merge_base") or ""
    # Deleted lines are numbered at the old merge base; without it they cannot be carried.
    known = bool(old_base) and ensure_commit(old_base)
    new_base = git_text([*GIT, "rev-parse", MERGE_BASE_REF]).strip()
    names = [*GIT, "diff", "--name-only", "--no-renames"]
    paths = set(git_text([*names, new_base, HEAD_REF]).splitlines())
    paths |= set(git_text([*names, old_base if known else new_base, old_head]).splitlines())
    return {
        "added": line_map(old_head, git_text([*GIT, "rev-parse", HEAD_REF]).strip(), paths),
        "deleted": line_map(old_base, new_base, paths) if known else None,
    }


def changed_lines(paths):
    """Each of ``paths``' changed lines at the pinned head, with what renders them."""
    found = {}
    ordered = sorted(paths)
    for at in range(0, len(ordered), PATHS_PER_CALL):
        path = ""
        hunk = -1
        old = new = 0
        diff = [*DIFF, MERGE_BASE_REF, HEAD_REF, "--", *ordered[at : at + PATHS_PER_CALL]]
        for line in git_lines(diff):
            if line.startswith("diff --git "):
                path = line.rpartition(" b/")[2]
                hunk = -1
                continue
            match = HUNK_RE.match(line)
            if match:
                old, hunk = int(match.group(1)), int(match.group(3))
                # A hunk that only deletes numbers its head side from the line before it.
                new = hunk if match.group(4) != "0" else hunk + 1
                continue
            if hunk < 0:
                if line.startswith("+++ b/"):
                    path = line[6:]
                continue
            if line.startswith("-"):
                found.setdefault(path, []).append(("-", old, line[1:], hunk, new - 1))
                old += 1
            elif line.startswith("+"):
                found.setdefault(path, []).append(("+", new, line[1:], hunk, new - 1))
                new += 1
            elif not line.startswith("\\"):
                old += 1
                new += 1
    return found


def contains(spans, lineno):
    lo, hi = 0, len(spans)
    while lo < hi:
        mid = (lo + hi) // 2
        if spans[mid][1] < lineno:
            lo = mid + 1
        else:
            hi = mid
    return lo < len(spans) and spans[lo][0] <= lineno


def selected(lines_by_path, line_set):
    """The changed lines ``line_set`` holds, per file in diff order."""
    chosen = {}
    for path, sides in line_set.items():
        picked = [
            line
            for line in lines_by_path.get(path, [])
            if contains(sides.get(line[0], []), line[1])
        ]
        if picked:
            chosen[path] = picked
    return chosen


def fenced(body, language=""):
    return f"```{language}\n{body.rstrip()}\n```" if body.strip() else "_(nothing to show)_"


def language_for(path):
    extension = posixpath.splitext(path)[1][1:].lower()
    return LANGUAGES.get(extension, extension)


def runs(lines):
    """Split chosen lines at hunk boundaries and where their numbering jumps."""
    out = []
    for line in lines:
        last = out[-1][-1] if out else None
        replacing = (
            last is not None and last[0] == "-" and line[0] == "+" and line[1] == last[4] + 1
        )
        contiguous = last is not None and last[0] == line[0] and line[1] == last[1] + 1
        if last is None or last[3] != line[3] or not (replacing or contiguous):
            out.append([])
        out[-1].append(line)
    return out


def context(text, changed, start, step):
    """Up to ``CONTEXT_LINES`` unchanged head line numbers walking from ``start`` by ``step``."""
    found = []
    lineno = start
    while len(found) < CONTEXT_LINES and 1 <= lineno <= len(text) and lineno not in changed:
        found.append(lineno)
        lineno += step
    return sorted(found)


def head_file(path, cache):
    if path not in cache:
        result = subprocess.run([*GIT, "show", HEAD_REF + ":" + path], capture_output=True)
        cache[path] = (
            result.stdout.decode("utf-8", errors="replace").splitlines()
            if result.returncode == 0
            else []
        )
    return cache[path]


def render_one(chosen, lines_by_path, files):
    blocks = []
    for path, path_lines in chosen.items():
        text = head_file(path, files)
        changed = {line[1] for line in lines_by_path.get(path, []) if line[0] == "+"}
        for run in runs(path_lines):
            plus = [line for line in run if line[0] == "+"]
            minus = [line for line in run if line[0] == "-"]
            if not minus:
                span = f"L{plus[0][1]}" + (f"–{plus[-1][1]}" if len(plus) > 1 else "")
                source = "\n".join(line[2] for line in plus)
                blocks.append(f"`{path}` {span}\n{fenced(source, language_for(path))}")
                continue
            first_new = plus[0][1] if plus else run[0][4] + 1
            last_new = plus[-1][1] if plus else run[0][4]
            before = context(text, changed, first_new - 1, -1)
            after = context(text, changed, last_new + 1, 1)
            new_start = before[0] if before else first_new
            old_start = minus[0][1] - len(before)
            old_count = len(before) + len(minus) + len(after)
            new_count = len(before) + len(plus) + len(after)
            body = [
                f"@@ -{old_start},{old_count} +{new_start},{new_count} @@",
                *(" " + text[n - 1] for n in before),
                *(line[0] + line[2] for line in run),
                *(" " + text[n - 1] for n in after),
            ]
            blocks.append(f"`{path}`\n{fenced(chr(10).join(body), 'diff')}")
    return "\n".join(blocks)


def render(request):
    sets = request["sets"]
    lines_by_path = changed_lines({path for line_set in sets for path in line_set})
    files = {}
    return {"codes": [render_one(selected(lines_by_path, s), lines_by_path, files) for s in sets]}


def wordless(request):
    sets = request["sets"]
    lines_by_path = changed_lines({path for line_set in sets for path in line_set})
    return {
        "wordless": [
            not any(
                WORD_RE.search(line[2])
                for path_lines in selected(lines_by_path, s).values()
                for line in path_lines
            )
            for s in sets
        ]
    }


OPS = {"index": index, "maps": maps, "render": render, "wordless": wordless}


def read_request(argument):
    """The request: base64 JSON, or ``@path`` to a file holding it when it is too long for argv."""
    if argument.startswith("@"):
        with open(argument[1:], "rb") as handle:
            data = handle.read()
        os.unlink(argument[1:])
        return json.loads(base64.b64decode(data).decode())
    return json.loads(base64.b64decode(argument).decode())


def main():
    request = read_request(sys.argv[1])
    try:
        os.chdir(request["repo"])
        result = OPS[request["op"]](request)
    except (Refused, OSError) as exc:
        result = {"error": str(exc)}
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
