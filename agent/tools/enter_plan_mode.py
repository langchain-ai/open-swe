from langgraph.types import Command


def enter_plan_mode() -> Command:
    """Activate plan mode mid-run.

    Call this when you believe the task would benefit from a structured
    implementation plan before writing any code — e.g. when the request is
    complex, touches many files, or has multiple valid approaches. This is
    NOT triggered by the word "plan" appearing in the request; use your
    judgment about whether planning is genuinely warranted.

    Once activated, mutating tools are removed and shell commands are
    restricted to read-only. Present your plan as a normal assistant message
    and stop — the user will review it and tell you to proceed.
    """
    return Command(update={"plan_mode": True})
