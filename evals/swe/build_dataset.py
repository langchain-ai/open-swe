#!/usr/bin/env python3
"""Build the SWE-bench private offline evaluation dataset.

Supports bootstrap seeding of realistic coding tasks (calculator bugs)
to ensure a fully self-contained, offline execution pipeline.

Usage:
    python evals/swe/build_dataset.py --bootstrap
"""

import argparse
import json
import sys
from pathlib import Path

# Resolve evaluation directories
EVALS_DIR = Path(__file__).resolve().parent
BENCHMARK_FILE = EVALS_DIR / "swe_benchmark.jsonl"

# 3 bootstrap coding cases representing typical calculator bugs
BOOTSTRAP_CASES = [
    {
        "id": "calc_bug_01",
        "title": "Division by Zero Bug",
        "task_description": "Fix bug in calculator where division by zero returns None instead of raising ZeroDivisionError.",
        "test_cmd": "uv run pytest tests/test_calculator.py::test_division_by_zero",
        "golden_patch": """diff --git a/calculator.py b/calculator.py
index a1b2c3d..e5f6g7h 100644
--- a/calculator.py
+++ b/calculator.py
@@ -10,3 +10,5 @@ def divide(x, y):
-    if y == 0:
-        return None
+    if y == 0:
+        raise ZeroDivisionError("division by zero")
     return x / y
""",
        "files": {
            "calculator.py": """def add(x, y):
    return x + y

def subtract(x, y):
    return x - y

def multiply(x, y):
    return x * y

def divide(x, y):
    if y == 0:
        return None
    return x / y
""",
            "tests/test_calculator.py": """import pytest
from calculator import add, subtract, multiply, divide

def test_arithmetic():
    assert add(2, 3) == 5
    assert subtract(5, 2) == 3
    assert multiply(3, 4) == 12
    assert divide(6, 2) == 3.0

def test_division_by_zero():
    with pytest.raises(ZeroDivisionError):
        divide(5, 0)
""",
        },
    },
    {
        "id": "calc_bug_02",
        "title": "Negative Exponent Power Bug",
        "task_description": "Fix bug in calculator power function where negative exponents return 0 instead of raising ValueError.",
        "test_cmd": "uv run pytest tests/test_calculator.py::test_negative_exponent",
        "golden_patch": """diff --git a/calculator.py b/calculator.py
index a1b2c3d..e5f6g7h 100644
--- a/calculator.py
+++ b/calculator.py
@@ -15,3 +15,5 @@ def power(x, y):
-    if y < 0:
-        return 0
+    if y < 0:
+        raise ValueError("negative exponent not supported")
     return x ** y
""",
        "files": {
            "calculator.py": """def add(x, y):
    return x + y

def power(x, y):
    if y < 0:
        return 0
    return x ** y
""",
            "tests/test_calculator.py": """import pytest
from calculator import add, power

def test_add():
    assert add(2, 3) == 5

def test_negative_exponent():
    with pytest.raises(ValueError):
        power(2, -3)
""",
        },
    },
    {
        "id": "calc_bug_03",
        "title": "Add Modulo Feature",
        "task_description": "Add a modulo (%) function called 'mod' to the calculator. It should take parameters x and y, returning the remainder, and raise ZeroDivisionError if y is 0.",
        "test_cmd": "uv run pytest tests/test_calculator.py::test_modulo",
        "golden_patch": """diff --git a/calculator.py b/calculator.py
index a1b2c3d..e5f6g7h 100644
--- a/calculator.py
+++ b/calculator.py
@@ -10,3 +10,7 @@ def divide(x, y):
+def mod(x, y):
+    if y == 0:
+        raise ZeroDivisionError("modulo by zero")
+    return x % y
""",
        "files": {
            "calculator.py": """def add(x, y):
    return x + y

def divide(x, y):
    if y == 0:
        raise ZeroDivisionError("division by zero")
    return x / y
""",
            "tests/test_calculator.py": """import pytest
from calculator import add, divide, mod

def test_calculator():
    assert add(2, 3) == 5
    assert divide(6, 2) == 3

def test_modulo():
    assert mod(5, 2) == 1
    assert mod(6, 3) == 0
    with pytest.raises(ZeroDivisionError):
        mod(5, 0)
""",
        },
    },
]


def main():
    parser = argparse.ArgumentParser(description="Build private SWE-bench evaluation dataset.")
    parser.add_argument(
        "--bootstrap", action="store_true", help="Generate default calculator bug benchmarks"
    )
    parser.add_argument(
        "--out", type=str, default=str(BENCHMARK_FILE), help="Output JSONL benchmark filepath"
    )
    args = parser.parse_args()

    if args.bootstrap:
        print(f"Generating {len(BOOTSTRAP_CASES)} bootstrap calculator bug cases...")
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with out_path.open("w") as f:
            for case in BOOTSTRAP_CASES:
                f.write(json.dumps(case) + "\n")

        print(f"Success! Benchmark dataset saved to {out_path}")
        sys.exit(0)

    # Standard mode: scans git logs (to be extended by developer)
    print("Normal git scan mode selected. (Add --bootstrap to quickly seed mock benchmarks)")


if __name__ == "__main__":
    main()
