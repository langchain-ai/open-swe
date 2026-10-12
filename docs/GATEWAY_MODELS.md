# Unlisted gateway models

Workspace admins can opt into unlisted models for selected LangSmith LLM Gateway providers using the authenticated settings API. The dashboard model picker continues to show the curated model catalog.

`PUT /dashboard/api/workspaces/{workspace}/settings` (prepend any deployment mount prefix) accepts `gateway_allowed_providers`: `openai`, `anthropic`, `baseten`, `fireworks`, or `google_genai`. Set `gateway_enabled` to `true` and configure a LangSmith gateway API key with invocation permission. The existing admin-only settings endpoint controls this policy; ordinary users cannot change it.

Example settings body:

```json
{
  "gateway_enabled": true,
  "gateway_allowed_providers": ["anthropic"],
  "default_agent_model": "anthropic:your-model-id",
  "default_agent_reasoning_effort": "none"
}
```

Settings PUTs replace the workspace's overrides, so preserve other overrides from its settings GET response. `null` inherits the instance provider policy; an empty list disables unlisted models for that workspace. Instance settings can establish an inherited policy too.

Clients can select an allowed `provider:model` using the existing thread `agent_model_id` override. Unlisted selections use provider-default reasoning regardless of the requested effort: Open SWE does not infer reasoning support from a provider name. They are treated as text-only for image fallback. Tool-calling support and model availability must be provided by the chosen model; provider permission does not guarantee either.

The curated catalog's capability and reasoning behavior remains unchanged. Removing a provider permission or disabling gateway routing prevents existing threads from continuing with that unlisted model. Unlisted models never silently fall back to direct provider credentials when gateway authentication is missing.
