"""Typed inputs, target outputs, and failure stages for coding evaluations."""

from enum import StrEnum

from pydantic import BaseModel, Field

from agent.sandboxes.environment import ExecutionEnvironment


class FailureStage(StrEnum):
    NONE = "none"
    AGENT_EXECUTION = "agent_execution"
    PATCH_COLLECTION = "patch_collection"
    INVALID_INSTANCE = "invalid_instance"
    CHECKOUT = "checkout"
    INSTALL = "install"
    AGENT_PATCH = "agent_patch"
    TEST_PATCH = "test_patch"
    TEST_EXECUTION = "test_execution"
    MISSING_OUTCOMES = "missing_outcomes"
    NO_PATCH = "no_patch"
    TARGET_TESTS = "target_tests"
    REGRESSION = "regression"


class EvalInputs(BaseModel):
    instance_id: str = Field(min_length=1)
    repo: str = Field(pattern=r"^[^/\s]+/[^/\s]+$")
    base_commit: str = Field(pattern=r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
    problem_statement: str = Field(min_length=1)
    clone_url: str | None = None


class GradingSpec(BaseModel):
    fail_to_pass: list[str] = Field(min_length=1)
    pass_to_pass: list[str] = Field(default_factory=list)
    test_patch: str = ""
    install_commands: list[str] = Field(default_factory=list)
    test_command: str = "pytest"


class CompactionTelemetry(BaseModel):
    """Run-level conversation compaction totals reported by the agent graph.

    Estimates come from the agent token counter; they cover messages replaced
    by summaries, not billed model usage.
    """

    count: int = Field(default=0, ge=0)
    messages_summarized: int = Field(default=0, ge=0)
    summarized_tokens: int = Field(default=0, ge=0)
    summary_tokens: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)


class TargetResult(BaseModel):
    instance_id: str = ""
    repo: str = ""
    thread_id: str = ""
    patch: str = ""
    steps: int | None = Field(default=None, ge=0)
    wall_clock_seconds: float = Field(default=0.0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    usage_complete: bool = False
    failure_stage: FailureStage = FailureStage.NONE
    error: str = ""
    execution_environment: ExecutionEnvironment | None = None
    compaction: CompactionTelemetry | None = None
