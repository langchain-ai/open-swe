# Sandbox scripts

Everything here is copied into a thread's sandbox and run with its `python3`.
Put any non-trivial in-sandbox logic here as a real script, never as shell or Python built up in f-strings.

- Standard library only, and Python 3.8 syntax.
  `make lint` (ruff `per-file-target-version`) and `make typecheck` (`ty --python-version 3.8`) check this directory at that version.
- The backend side lives in `openswe/sandboxes/scripts.py`.
  Keep it out of this directory, which is held to 3.8.
- Run a script with `SandboxScript("<name>.py", root).run(backend, request)`.
  It keeps a copy in `root` named by the script's hash, writes the copy only when that version is missing, passes `request` as base64 JSON, and returns the one JSON line the script prints.
  A script that answers `{"error": ...}` raises `SandboxScriptError`.
- A request too long for a command line (over 64 KiB encoded) is refused unless the script is made with `reads_request_files=True`; then it is written to a file and the script gets `@<path>`, which it must read and delete, as `walkthrough.py` does.
  Root-relative scripts (`monitor.py`, `background_task.py`) derive their root from their own location.
- A one-off script can be read with `source("<name>.py")` and run as a heredoc with its inputs substituted for a `__PAYLOAD__` marker, as `recovery_patch.py` and `workspace_path.py` are.
- Test a script by running it locally through the real client against a temp directory, as `tests/tools/test_monitor.py` does.
