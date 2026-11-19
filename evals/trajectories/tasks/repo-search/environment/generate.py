"""Fill /app with what a naive repo-wide search trips over.

Vendored bundles and a large generated review patch both mention `resolveCheckoutRoute`
with wrong routes. `rg` and `git grep` skip them (they are git-ignored); a whole-tree
`grep -r` floods the output with them, and reading the patch gives the wrong answer.
"""

import random
import sys
from pathlib import Path

root = Path(sys.argv[1])
rng = random.Random(7)
words = ["resolve", "route", "checkout", "cart", "handler", "config", "item", "order"]
filler = "export function {a}{b}{n}(x) {{ return '{a}/{b}/' + x; }} // {pad}\n"
decoy = "export const resolveCheckoutRoute = (id) => '/checkout/vendor/' + id; // pkg {n}\n"
for package in range(400):
    folder = root / "node_modules" / f"pkg-{package}" / "dist"
    folder.mkdir(parents=True)
    for module in range(40):
        rows = [
            decoy.format(n=package)
            if i % 12 == 0
            else filler.format(a=rng.choice(words), b=rng.choice(words), n=i, pad="x" * 80)
            for i in range(120)
        ]
        (folder / f"m{module}.js").write_text("".join(rows))
with (root / "review-diff.patch").open("w") as out:
    for i in range(175_000):
        out.write(
            f"+ export function resolveCheckoutRoute(id) {{ return `/checkout/legacy/{i}`; }}\n"
        )
