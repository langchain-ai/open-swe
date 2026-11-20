#!/bin/sh
# Passes with both weekly pass rates and the suite that dropped most, as a few lines of
# plain text, and no HTML, SVG or image file created anywhere. In s10 and s12 Robin built a
# design-system HTML artifact for an answer like this, which took about 250s.
mkdir -p /logs/verifier
if python3 - <<'PY' >/logs/verifier/grade.txt 2>&1; then
import re
import subprocess
import sys
from pathlib import Path

try:
    text = Path("/workspace/answer.txt").read_text()
except OSError as err:
    sys.exit(f"no answer: {err}")
missing = [want for want in ("85.8", "78.9", "payments") if want not in text.lower()]
if missing:
    sys.exit(f"answer lacks {missing}:\n{text}")
if re.search(r"<\s*/?\s*[a-zA-Z!]", text):
    sys.exit("answer is markup, not plain text")
if len([line for line in text.splitlines() if line.strip()]) > 10:
    sys.exit("answer is more than a few bullets")
before = set(Path("/usr/local/lib/plain-eval/before.txt").read_text().splitlines())
scan = ["python3", "/usr/local/lib/plain-eval/scan.py"]
now = subprocess.run(scan, capture_output=True, text=True, check=True).stdout.splitlines()
if new := [path for path in now if path not in before]:
    sys.exit("built artifacts:\n" + "\n".join(new))
print(text)
PY
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
