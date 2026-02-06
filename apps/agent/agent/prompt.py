SYSTEM_PROMPT = """### Current Working Directory

You are operating in a **remote Linux sandbox** at `{working_dir}`.

All code execution and file operations happen in this sandbox environment.

**Important:**
- Use `{working_dir}` as your working directory for all operations

"""


def construct_system_prompt(working_dir: str) -> str:
    return SYSTEM_PROMPT.format(working_dir=working_dir)
