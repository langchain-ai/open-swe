from pathlib import Path

import local_fix_agent as lfa


REPO_ROOT = Path("/home/tom/ai/open-swe")


def test_analyze_validation_failure_syntax_error_targets_source_file() -> None:
    analysis = lfa.analyze_validation_failure(
        REPO_ROOT,
        validation_command="python -m py_compile local_fix_agent.py",
        validation_output='File "local_fix_agent.py", line 12\nSyntaxError: invalid syntax\n',
    )

    assert analysis["validation_error_type"] == "syntax"
    assert analysis["failing_source_files"] == ["local_fix_agent.py"]
    assert analysis["repair_targets"][0] == "local_fix_agent.py"
    assert analysis["repair_context_used"] is True
    assert analysis["traceback_files"] == ["local_fix_agent.py"]
    assert analysis["failure_line_numbers"] == [12]
    assert analysis["repair_target_details"][0]["target_path"] == "local_fix_agent.py"
    assert analysis["repair_target_details"][0]["target_type"] == "source"
    assert analysis["repair_target_details"][0]["target_confidence"] == "high"
    assert analysis["target_confidence"] == "high"


def test_analyze_validation_failure_import_error_prefers_imported_module() -> None:
    output = """
ImportError while importing test module '/home/tom/ai/open-swe/tests/test_local_fix_agent_publish.py'.
Traceback:
File "/home/tom/ai/open-swe/tests/test_local_fix_agent_publish.py", line 5, in <module>
    from local_fix_agent import missing_symbol
ImportError: cannot import name 'missing_symbol' from 'local_fix_agent'
"""
    analysis = lfa.analyze_validation_failure(
        REPO_ROOT,
        validation_command="pytest tests/test_local_fix_agent_publish.py -q",
        validation_output=output,
    )

    assert analysis["validation_error_type"] == "import"
    assert "tests/test_local_fix_agent_publish.py" in analysis["failing_test_files"]
    assert "local_fix_agent.py" in analysis["failing_source_files"]
    assert analysis["repair_targets"][0] == "local_fix_agent.py"
    assert analysis["repair_target_details"][0]["target_path"] == "local_fix_agent.py"
    assert analysis["repair_target_details"][0]["target_confidence"] == "high"
    assert "import" in analysis["target_reason"]


def test_analyze_validation_failure_pytest_failure_captures_test_and_source_files() -> None:
    output = """
FAILED tests/test_local_fix_agent_publish.py::test_publish_summary
Traceback (most recent call last):
  File "/home/tom/ai/open-swe/tests/test_local_fix_agent_publish.py", line 10, in test_publish_summary
    result = build()
  File "/home/tom/ai/open-swe/local_fix_agent.py", line 145, in build
    assert actual == expected
AssertionError
E       assert 'actual' == 'expected'
"""
    analysis = lfa.analyze_validation_failure(
        REPO_ROOT,
        validation_command="pytest tests/test_local_fix_agent_publish.py -q",
        validation_output=output,
    )

    assert analysis["validation_error_type"] == "assertion_mismatch"
    assert analysis["failing_test_files"] == ["tests/test_local_fix_agent_publish.py"]
    assert analysis["failing_source_files"][0] == "local_fix_agent.py"
    assert analysis["repair_targets"][0] == "local_fix_agent.py"
    assert analysis["traceback_files"] == ["tests/test_local_fix_agent_publish.py", "local_fix_agent.py"]
    assert analysis["failure_line_numbers"] == [10, 145]
    assert analysis["repair_target_details"][0]["target_path"] == "local_fix_agent.py"
    assert analysis["repair_target_details"][0]["target_confidence"] in {"high", "medium"}
    assert analysis["repair_target_details"][1]["target_type"] == "test"


def test_analyze_validation_failure_inconclusive_falls_back_to_generic_repair() -> None:
    analysis = lfa.analyze_validation_failure(
        REPO_ROOT,
        validation_command="custom-check",
        validation_output="validation command exited 1 with no traceback",
    )

    assert analysis["validation_error_type"] in {"command_failure", "unknown"}
    assert analysis["repair_targets"] == []
    assert analysis["repair_context_used"] is False
    assert analysis["repair_target_details"] == []
    assert analysis["target_confidence"] == "low"


def test_analyze_validation_failure_pytest_failed_line_captures_test_file() -> None:
    output = "FAILED tests/test_local_fix_agent_publish.py::test_publish_summary\nAssertionError"
    analysis = lfa.analyze_validation_failure(
        REPO_ROOT,
        validation_command="pytest tests/test_local_fix_agent_publish.py -q",
        validation_output=output,
    )

    assert analysis["failing_test_files"] == ["tests/test_local_fix_agent_publish.py"]
    assert analysis["repair_target_details"][0]["target_type"] == "test"
    assert analysis["repair_target_details"][0]["target_confidence"] == "medium"


def test_analyze_validation_failure_traceback_prefers_source_file_when_present() -> None:
    output = """
Traceback (most recent call last):
  File "/home/tom/ai/open-swe/tests/test_local_fix_agent_publish.py", line 20, in test_x
    value = build()
  File "/home/tom/ai/open-swe/local_fix_agent.py", line 200, in build
    raise NameError("bad")
NameError: bad
"""
    analysis = lfa.analyze_validation_failure(
        REPO_ROOT,
        validation_command="pytest tests/test_local_fix_agent_publish.py -q",
        validation_output=output,
    )

    assert analysis["validation_error_type"] == "command_failure"
    assert analysis["traceback_files"] == ["tests/test_local_fix_agent_publish.py", "local_fix_agent.py"]
    assert analysis["failure_line_numbers"] == [20, 200]
    assert analysis["repair_target_details"][0]["target_path"] == "local_fix_agent.py"
    assert analysis["repair_target_details"][0]["target_type"] == "source"
