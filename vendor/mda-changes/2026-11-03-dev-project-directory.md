# Upstream as-is.

**What:** `_local_dev_deployment_name` in `packages/pypi/src/managed_deepagents/runtime.py` reads the project directory once at import (`_PROJECT_DIRECTORY`) instead of calling `Path.cwd()` on every call.

**Why:** under `mda dev`, every browser and sandbox name goes through it, and the browser middleware asks for the browser's name at the start of each run, on the event loop. `os.getcwd()` there is a blocking call, so `langgraph dev` failed every coding-agent run in its first step with `BlockingError: Blocking call to os.getcwd`. The directory cannot change while the server runs, so one read is enough. Deployments never set `MDA_LOCAL_DEV` and were unaffected.

**Upstream:** propose it unchanged; drop ours once `main` reads the directory off the event loop.
