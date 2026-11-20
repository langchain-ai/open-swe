"""Lists the HTML, SVG and image files on the filesystem, for the grader to diff."""

import os
import sys

SKIP = {"/proc", "/sys", "/dev", "/logs", "/tests", "/solution"}
SUFFIXES = (".html", ".htm", ".svg", ".png", ".pdf")

found = []
for root, dirs, files in os.walk("/"):
    dirs[:] = [d for d in dirs if os.path.join(root, d) not in SKIP]
    found += [os.path.join(root, f) for f in files if f.lower().endswith(SUFFIXES)]
sys.stdout.write("".join(f"{path}\n" for path in sorted(found)))
