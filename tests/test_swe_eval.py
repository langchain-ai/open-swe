import subprocess

import pytest

from evals.swe.run_eval import (
    calculate_token_cost,
    compute_ast_similarity,
    load_config,
    setup_case_sandbox,
)


def test_load_config():
    """Verify that configuration is loaded successfully."""
    config = load_config()
    assert config is not None
    assert "benchmark" in config
    assert "metrics" in config


def test_ast_similarity_identical():
    code_a = """
def add(x, y):
    return x + y
"""
    code_b = """
def add(a, b):
    return a + b
"""
    # Identical AST structures (same node classes) -> should be 1.0 similarity
    assert compute_ast_similarity(code_a, code_b) == 1.0


def test_ast_similarity_different():
    code_a = """
def add(x, y):
    return x + y
"""
    code_b = """
class Calculator:
    def __init__(self):
        pass
    def subtract(self, x, y):
        return x - y
"""
    # Different structural nodes -> should be < 1.0
    score = compute_ast_similarity(code_a, code_b)
    assert score < 1.0
    assert score >= 0.0


def test_ast_similarity_invalid_syntax():
    code_a = "def add(x, y): return x +"
    code_b = "x = 5"
    # Invalid syntax should compile gracefully to 0.0 similarity
    assert compute_ast_similarity(code_a, code_b) == 0.0


def test_calculate_token_cost():
    prompt = "a" * 400  # ~100 tokens
    response = "b" * 200  # ~50 tokens

    tokens, cost = calculate_token_cost(prompt, response)
    assert tokens == 150
    assert cost > 0.0


@pytest.mark.asyncio
async def test_setup_case_sandbox(tmp_path):
    """Test that evaluation temporary sandbox files and git initialisation are correct."""
    case = {
        "id": "test_case_01",
        "files": {"main.py": "print('hello')", "tests/test_main.py": "assert True"},
    }

    case_dir = tmp_path / "case_01"
    await setup_case_sandbox(case, case_dir)

    assert case_dir.is_dir()
    assert (case_dir / "main.py").is_file()
    assert (case_dir / "tests" / "test_main.py").is_file()

    # Verify local git repo initialized
    git_dir = case_dir / ".git"
    assert git_dir.is_dir()

    res = subprocess.run(["git", "status"], cwd=case_dir, capture_output=True, text=True)
    assert res.returncode == 0
    assert "working tree clean" in res.stdout.lower()
