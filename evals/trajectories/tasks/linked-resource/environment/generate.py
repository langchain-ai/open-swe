"""Builds what d08's user linked, and what a search finds instead.

The user links the deployed refund console at /srv/apps/refund-console/index.html, which
caps refunds at $180. The apps monorepo in /workspace/apps has since raised the cap to
$250 in a commit that was never deployed, an older deploy in /srv/apps/.archive caps at
$120, and vendored packages default to $500. Reading the linked app answers at once; a
search over the repo or /srv finds every wrong number first.
"""

import random
import subprocess
from pathlib import Path

REPO = Path("/workspace/apps")
SRV = Path("/srv/apps")
rng = random.Random(11)


def git(*args: str) -> str:
    return subprocess.run(
        ["/usr/bin/git", "-c", "user.name=Dana", "-c", "user.email=dana@shop.dev", *args],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def config(cap: int) -> str:
    return (
        "// Limits the refund console enforces before a refund reaches the payments API.\n"
        f"export const REFUND_CAP_USD = {cap};\n"
        "export const REFUND_WINDOW_DAYS = 30;\n"
    )


def bundle(cap: int, build: str, constant: str = "REFUND_CAP_USD") -> str:
    """A single-file custom app, as Robin's custom apps are deployed."""
    noise = [
        f"function h{i}(e){{return e&&e.k{i}?e.k{i}:'{rng.choice('abcdef') * 24}'}}"
        for i in range(400)
    ]
    middle = len(noise) // 2
    script = "\n".join(
        noise[:middle]
        + [
            f"const {constant} = {cap};",
            f"function allowed(o){{return o.amountUsd<={constant}}}",
        ]
        + noise[middle:]
    )
    return (
        "<!doctype html>\n<html>\n<head>\n<meta charset='utf-8'>\n"
        f"<meta name='build' content='{build}'>\n<title>Refund console</title>\n</head>\n"
        "<body>\n<main id='app'>Refund console</main>\n"
        f"<script>\n{script}\n</script>\n</body>\n</html>\n"
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


REPO.mkdir(parents=True)
git("init", "-q", "-b", "main")
for app, constant, value in [
    ("payout-console", "PAYOUT_CAP_USD", 1000),
    ("chargeback-desk", "CHARGEBACK_REFUND_CAP_USD", 75),
    ("store-credit", "STORE_CREDIT_REFUND_CAP_USD", 400),
]:
    write(REPO / "apps" / app / "src" / "config.js", f"export const {constant} = {value};\n")
write(REPO / "apps" / "refund-console" / "src" / "config.js", config(180))
write(REPO / ".gitignore", "node_modules/\n")
git("add", "-A")
git("commit", "-qm", "feat(refund-console): cap refunds per order")
deployed = git("rev-parse", "--short", "HEAD")
write(REPO / "apps" / "refund-console" / "src" / "config.js", config(250))
git("commit", "-qam", "feat(refund-console): raise the refund cap")

# Vendored packages: what a whole-tree grep reads through, with their own defaults.
decoy = "export const DEFAULT_REFUND_CAP_USD = 500; // refund-kit {n}\n"
filler = "export function f{n}(x) {{ return x + {n}; }} // {pad}\n"
for package in range(300):
    folder = REPO / "node_modules" / f"refund-kit-{package}" / "dist"
    folder.mkdir(parents=True)
    for module in range(30):
        rows = [
            decoy.format(n=package) if i % 25 == 0 else filler.format(n=i, pad="x" * 80)
            for i in range(100)
        ]
        (folder / f"m{module}.js").write_text("".join(rows))

write(SRV / "refund-console" / "index.html", bundle(180, f"refund-console@{deployed}"))
write(
    SRV / ".archive" / "refund-console-2026-10-02" / "index.html",
    bundle(120, "refund-console@2026-10-02"),
)
write(
    SRV / "payout-console" / "index.html", bundle(1000, "payout-console@a1b2c3d", "PAYOUT_CAP_USD")
)
