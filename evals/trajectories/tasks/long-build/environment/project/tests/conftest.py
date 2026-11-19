import os
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

if not os.environ.get("SKIP_BUILD_CHECK") and not (ROOT / "build" / "ok").exists():
    raise SystemExit("build/ok is missing: run `make build` first")
