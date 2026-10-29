# Resolve tools code calls that no installed tool names

**Verdict: upstream as-is.** A generic hook for any agent whose tools are found by search instead of listed.

**Changed:** `langchain_quickjs/middleware.py`, `langchain_quickjs/_repl.py`, `langchain_quickjs/_ptc.py`, `langchain_quickjs/__init__.py` (exports `ToolResolver` and `UnresolvedToolError`).

## What

`CodeInterpreterMiddleware(resolve_tool=...)` takes a `ToolResolver`, `Callable[[str], BaseTool | None]`.
With one, `globalThis.tools` is a `Proxy` over the installed tools: `tools.<name>` for a name `ptc` installed none by asks the resolver, with the camelCase name, and calls the tool it returns as it calls an installed one (the same call budget, injected runtime and outer loop).
A name it returns `None` for throws `TypeError: tools.<name> is not a function` in JavaScript.
A resolver that can only find out by calling something returns a tool that looks itself up when called; that tool raises `UnresolvedToolError` for a name that turns out to name nothing, and the call throws `TypeError` with its message.
`then` and `toJSON` stay undefined, so `await tools` is not taken for a promise and `JSON.stringify(tools)` calls no tool.
The prompt's API reference still lists only the installed tools.

## Why

An Open SWE run offers hundreds of tools from code, most of them integrations it seldom calls.
Listing each in the run's catalog put about 1 MB in every run's context, which the Agent Server copies into every checkpoint and run.
The run now finds them with the backend's `search_tools` and calls them by the name a search returned, which code mode cannot do unless a call can reach a tool it never installed.

## Upstream

The hook as it stands.
