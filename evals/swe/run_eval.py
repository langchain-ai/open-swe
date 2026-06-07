#!/usr/bin/env python3
"""SWE-bench private offline evaluation runner.

Runs the multi-agent graph concurrently across isolated sandboxes,
running unit tests and calculating Pass@1, Token Cost, and AST Similarity metrics.

Usage:
    python evals/swe/run_eval.py --limit 3
"""

# ruff: noqa: E402
import os
import sys

from dotenv import load_dotenv

load_dotenv()

import argparse
import ast
import asyncio
import json
import shutil
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

# Add workspace root to python path to allow importing agent modules
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))


def remove_readonly(func, path, excinfo):
    """Clear the read-only bit on Windows files (like git objects) and retry deletion."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def cleanup_dir(dir_path: Path):
    """Robustly cleanup temporary case directory, retrying on transient Windows locks."""
    if not dir_path.exists():
        return
    for attempt in range(3):
        try:
            shutil.rmtree(dir_path, onerror=remove_readonly)
            return
        except Exception as e:
            if attempt < 2:
                time.sleep(0.5)
            else:
                print(f"Warning: Failed to clean up {dir_path}: {e}")


from langchain_core.messages import HumanMessage
from langgraph.graph.state import RunnableConfig

from agent.multi_agent.graph import get_multi_agent_graph

# Load configurations
CONFIG_FILE = Path(__file__).resolve().parent / "config.toml"
BENCHMARK_FILE = Path(__file__).resolve().parent / "swe_benchmark.jsonl"
SCRATCH_DIR = Path(__file__).resolve().parent / "scratch"

EVALS_DIR = Path(__file__).resolve().parent

# Fallback config values
CONCURRENCY_LIMIT = 2
INPUT_TOKEN_PRICE = 0.15 / 1000000  # Per token
OUTPUT_TOKEN_PRICE = 0.60 / 1000000


def load_config() -> dict:
    try:
        import tomllib  # Python 3.11+

        with CONFIG_FILE.open("rb") as f:
            return tomllib.load(f)
    except Exception:
        # Fallback dictionary
        return {
            "benchmark": {"dataset_path": str(BENCHMARK_FILE), "scratch_dir": str(SCRATCH_DIR)},
            "metrics": {"input_token_price": 0.15, "output_token_price": 0.60},
        }


def compute_ast_similarity(code_a: str, code_b: str) -> float:
    """Compare two code strings using AST structures.

    Returns a similarity score between 0.0 and 1.0.
    """
    try:
        tree_a = ast.parse(code_a)
        tree_b = ast.parse(code_b)
    except Exception:
        return 0.0

    def get_node_types(tree) -> list[str]:
        types = []
        for node in ast.walk(tree):
            types.append(node.__class__.__name__)
        return types

    nodes_a = get_node_types(tree_a)
    nodes_b = get_node_types(tree_b)

    if not nodes_a or not nodes_b:
        return 0.0

    # Calculate intersection score
    set_a, set_b = set(nodes_a), set(nodes_b)
    intersection = set_a.intersection(set_b)
    union = set_a.union(set_b)

    return len(intersection) / len(union) if union else 0.0


def calculate_token_cost(prompt_text: str, response_text: str) -> tuple[int, float]:
    """Estimate token count and costs based on character lengths."""
    # Approximate 1 token = 4 characters
    input_tokens = len(prompt_text) // 4
    output_tokens = len(response_text) // 4
    total_tokens = input_tokens + output_tokens

    cost = (input_tokens * INPUT_TOKEN_PRICE) + (output_tokens * OUTPUT_TOKEN_PRICE)
    return total_tokens, cost


async def setup_case_sandbox(case: dict[str, Any], case_dir: Path):
    """Seed target buggy files and initialize local git repo in temp workspace."""
    cleanup_dir(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)

    # Write files
    for filename, content in case.get("files", {}).items():
        file_path = case_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)

    # Initialize Git
    subprocess.run(["git", "init"], cwd=case_dir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "swe-eval"], cwd=case_dir, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "eval@openswe.org"], cwd=case_dir, capture_output=True
    )
    subprocess.run(["git", "add", "."], cwd=case_dir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "buggy implementation"], cwd=case_dir, capture_output=True
    )


async def evaluate_case(
    case: dict[str, Any], semaphore: asyncio.Semaphore, single_agent: bool = False
) -> dict[str, Any]:
    """Execute a single evaluation case in an isolated sandbox using either single-agent or multi-agent graph."""
    async with semaphore:
        case_id = case["id"]
        mode_str = "Single-Agent" if single_agent else "Multi-Agent"
        print(f"Starting evaluation case: {case_id} ({case['title']}) [{mode_str}]")

        # Determine temporary workspace path
        case_dir = SCRATCH_DIR / case_id
        await setup_case_sandbox(case, case_dir)

        # Set environment variables to bind the local sandbox to this directory
        os.environ["LOCAL_SANDBOX_ROOT_DIR"] = str(case_dir)
        os.environ["SANDBOX_TYPE"] = "local"

        use_multi_agent = not single_agent
        config: RunnableConfig = {
            "configurable": {
                "thread_id": f"eval-{case_id}",
                "use_multi_agent": use_multi_agent,
                "agent_model_id": "openai:deepseek-v4-flash",
                "__is_for_execution__": True,
            },
            "metadata": {},
        }

        # Directly instantiate the local sandbox backend bound to the case directory
        # This bypasses the need for the LangGraph server to be running or connected during offline evaluation.
        from deepagents.backends import LocalShellBackend

        from agent.utils.sandbox_state import set_sandbox_backend

        local_backend = LocalShellBackend(root_dir=str(case_dir), inherit_env=True)
        set_sandbox_backend(config["configurable"]["thread_id"], local_backend)

        # Assemble and compile the appropriate graph
        if use_multi_agent:
            graph = get_multi_agent_graph(config)
        else:
            from agent.server import get_agent

            graph = await get_agent(config)

        start_time = time.time()

        # Invoke LangGraph pipeline
        try:
            if use_multi_agent:
                res_state = await graph.ainvoke(
                    {
                        "messages": [HumanMessage(content=case["task_description"])],
                        "task_description": case["task_description"],
                        "config": {"coder_retries": 0},
                    },
                    config,
                )
            else:
                res_state = await graph.ainvoke(
                    {"messages": [HumanMessage(content=case["task_description"])]}, config
                )

            error_msg = ""
        except Exception as e:
            res_state = {}
            import traceback

            traceback.print_exc()
            error_msg = str(e)
            print(f"  [ERROR] Error during graph execution for {case_id}: {e}")

        execution_time = time.time() - start_time

        # 1. Metric: Pass@1
        # Run test command in local scratch sandbox directory
        # Set PYTHONPATH to case_dir so pytest can resolve calculator imports correctly
        test_env = {**os.environ, "PYTHONPATH": str(case_dir)}
        test_res = subprocess.run(
            case["test_cmd"].split(), cwd=case_dir, capture_output=True, text=True, env=test_env
        )
        passed = test_res.returncode == 0

        # 2. Metric: AST Similarity
        # Compare calculator.py generated vs expected patch if possible
        calc_file = case_dir / "calculator.py"
        ast_score = 0.0
        if calc_file.exists():
            final_code = calc_file.read_text()
            # Golden patch is represented by BOOTSTRAP_CASES. Compare vs generated
            # We can parse ast and estimate structure similarity
            # Create a simple mock golden file representing expected correct code
            ast_score = compute_ast_similarity(final_code, case.get("golden_patch", ""))

        # 3. Metric: Token & Cost Estimation
        total_prompt = case["task_description"]
        if use_multi_agent:
            total_response = res_state.get("test_plan", "") + res_state.get("modified_diffs", "")
        else:
            assistant_msgs = []
            if "messages" in res_state:
                for msg in res_state["messages"]:
                    if getattr(msg, "type", "") == "ai" or getattr(msg, "role", "") == "assistant":
                        assistant_msgs.append(msg.content)
            try:
                diff_res = local_backend.execute("git diff")
                modified_diffs = diff_res.output.strip()
            except Exception:
                modified_diffs = ""
            total_response = "\n".join(assistant_msgs) + "\n" + modified_diffs
        tokens, cost = calculate_token_cost(total_prompt, total_response)

        # Cleanup scratch workspace directory
        cleanup_dir(case_dir)

        print(
            f"Finished case {case_id} | Pass: {passed} | AST: {ast_score:.2f} | Cost: ${cost:.5f}"
        )

        return {
            "case_id": case_id,
            "title": case["title"],
            "passed": passed,
            "ast_similarity": ast_score,
            "tokens": tokens,
            "cost": cost,
            "duration": execution_time,
            "error": error_msg,
        }


async def run_pipeline(limit: int | None, single_agent: bool = False):
    # Load cases
    cases = []
    if BENCHMARK_FILE.is_file():
        with BENCHMARK_FILE.open() as f:
            for line in f:
                if line.strip():
                    cases.append(json.loads(line))

    if limit:
        cases = cases[:limit]

    if not cases:
        print("No cases loaded. Run 'python evals/swe/build_dataset.py --bootstrap' first.")
        return

    mode_str = "Single-Agent" if single_agent else "Multi-Agent"
    print(
        f"Running SWE-bench parallel evaluation pipeline on {len(cases)} cases in [{mode_str}] mode..."
    )

    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    tasks = [evaluate_case(case, semaphore, single_agent) for case in cases]
    results = await asyncio.gather(*tasks)

    # Compile Summary
    total_cases = len(results)
    passed_cases = sum(1 for r in results if r["passed"])
    pass_rate = (passed_cases / total_cases) * 100 if total_cases else 0.0
    total_cost = sum(r["cost"] for r in results)
    avg_ast = sum(r["ast_similarity"] for r in results) / total_cases if total_cases else 0.0
    avg_duration = sum(r["duration"] for r in results) / total_cases if total_cases else 0.0

    print("\n" + "=" * 80)
    print(f"=== SWE-BENCH PRIVATE EVALUATION REPORT ({mode_str.upper()}) ===")
    print("=" * 80)
    print(f"Total Cases Run:    {total_cases}")
    print(f"Pass@1 Success:     {pass_rate:.1f}% ({passed_cases}/{total_cases})")
    print(f"Avg AST Similarity: {avg_ast:.2f}")
    print(f"Total Token Cost:   ${total_cost:.5f}")
    print(f"Avg Execution Time: {avg_duration:.1f}s")
    print("=" * 80)

    # Save report
    report_file_name = "eval_report_single.json" if single_agent else "eval_report.json"
    report_file = EVALS_DIR / report_file_name
    report_data = {
        "summary": {
            "total_cases": total_cases,
            "passed_cases": passed_cases,
            "pass_rate_percentage": pass_rate,
            "avg_ast_similarity": avg_ast,
            "total_token_cost": total_cost,
            "avg_duration_seconds": avg_duration,
        },
        "results": results,
    }
    with report_file.open("w") as f:
        json.dump(report_data, f, indent=2)
    print(f"Report saved successfully to {report_file}\n")


def main():
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    parser = argparse.ArgumentParser(description="Run SWE-bench private evaluation runner.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of test cases")
    parser.add_argument(
        "--single", action="store_true", help="Run in single-agent mode instead of multi-agent"
    )
    args = parser.parse_args()

    # Load configuration
    load_config()

    # Run async pipeline
    asyncio.run(run_pipeline(args.limit, single_agent=args.single))


if __name__ == "__main__":
    main()
