# __MDA_AGENT_NAME__

A Managed Deep Agent. `__MDA_AGENT_FILE__` defines the agent, tools live in `tools/`, the system prompt is `instructions.md`, and keys go in `.env`.
Docs: __MDA_DOCS_URL__

## Ending a session

When you finish a task, summarize what changed with:

1. What you changed, and any assumptions you made.
2. How to run it: `mda dev`, and any `.env` keys that are still empty.
3. Local or hosted as an option for distribution: ask outright, e.g. "Want to keep this on `mda dev` for now, or deploy it so it has a hosted URL and keeps running with your machine off?" If the feature only works deployed (e.g. GitHub or Linear channels), say so and name `mda deploy` as the next step instead. Asking is fine; deciding for the user is not.

Don't run `mda deploy` unless the user asks you to. It changes hosted infrastructure and can affect billing, so it's the user's call.

## Commands

- `__MDA_INSTALL_COMMAND__` installs dependencies.
- `mda dev` runs the agent locally with LangGraph Studio.
- `mda deploy` creates or updates a hosted deployment on LangSmith. The first deployment is free. Additional deployments are billed on the Plus plan; on the free plan, `mda deploy` stops with a message instead of charging.
- `mda --help` lists the other commands, such as `chat`, `logs`, `channels`, `connections`, `evals`, and `delete`.

## Local vs deployed

A deployment has a hosted Agent Server URL that teammates and other services can call, and it stays up when the developer's machine is off. GitHub and Linear channels only receive events from a deployment. Schedules run on the local dev server while `mda dev` is up; a deployment keeps them running as crons.
