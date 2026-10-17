# Sandbox scripts

Everything here is copied into a thread's sandbox and run with its `python3`. Put any non-trivial in-sandbox logic here as a real script, never as shell or Python built up in f-strings.

- Standard library only, and Python 3.8 syntax. `make lint` (ruff `per-file-target-version`) and `make typecheck` (`ty --python-version 3.8`) check this directory at that version.
- Read a script with `openswe.sandbox_scripts.source("<name>.py")`. Pass inputs as base64 JSON, either as an argument or substituted for a `__PAYLOAD__` marker. Print one JSON line as the result.
- A script the sandbox keeps between calls is written under a name with its content hash, so the sandbox always runs the version the backend ships. See `openswe/monitors/sandbox.py`.
- Test a script by running it locally through the real client against a temp directory, as `tests/tools/test_monitor.py` does.
