---
title: 'Authentication Setup'
description: 'Configure authentication for Open Agent Platform'
---

The default authentication provider Open Agent Platform is configured to use is Supabase.

## Setting Up Supabase

To set up Supabase authentication, you must first create a new Supabase project. After creating a new project, set the following environment variables inside the `apps/web/` directory:

```bash
NEXT_PUBLIC_SUPABASE_URL="<your supabase url>"
NEXT_PUBLIC_SUPABASE_ANON_KEY="<your supabase anon key>"
```

You should also enable Google authentication in your Supabase project. If you do not want to allow signing up with Google, set `NEXT_PUBLIC_GOOGLE_AUTH_DISABLED=true` in your environment variables to disable showing Google as an authentication option in the UI.

## LangGraph Server Authentication

Since the pre-built LangGraph agents implement custom authentication, there is no need to specify a LangSmith API key when making requests to them. Instead, we pass the user's Supabase access token (JWT token) in the `Authorization` header of the request.

Inside the auth middleware of the LangGraph server, we extract this token and verify it's valid with Supabase. If it is, we receive back a user ID, which is used to verify each user is *only* able to access their own agents and threads. If you want to allow users access to agents they did not create, you should update the custom authentication middleware in the LangGraph server to allow access to the agents you want users to be able to access.

Along with the `Authorization` header, we duplicate passing the Supabase JWT via the `x-supabase-access-token` header. This is because all non-LangSmith specific headers which are sent to LangGraph servers which are prefixed with `x-` are included in the configurable fields of the thread. We will need this JWT to later authenticate with the MCP server.

## LangSmith API Key Authentication

If you do *not* want to use custom authentication in your LangGraph server, and instead allow anyone to access your agents, you can do so by setting the `NEXT_PUBLIC_USE_LANGSMITH_AUTH` environment variable to `true`, and setting your `LANGSMITH_API_KEY` in the environment variables inside the `apps/web/` directory.

Lastly, ensure you have the `NEXT_PUBLIC_BASE_API_URL` environment variable set to the base API URL of your **web** server. For local development, this should be set to:

```bash
NEXT_PUBLIC_BASE_API_URL="http://localhost:3000/api"
```

This will cause all requests made to your web client to first pass through a proxy route, which injects the LangSmith API key into the request from the server, as to not expose the API key to the client. The request is then forwarded on to your LangGraph server.

<Warning>
Remember *not* to prefix your LangSmith API key environment variable with `NEXT_PUBLIC_`, as this is a **secret** and should never be exposed to the client.
</Warning>

## RAG Authentication

Authenticating to your LangConnect RAG server from the web client is handled in a similar way to LangGraph authentication. We pass the Supabase JWT in the `Authorization` header of the request. Then, inside the LangConnect RAG server, we extract this token and verify it's valid with Supabase.

If it is, we receive back a user ID, which is used to verify each user is *only* able to access their own collections. We do not currently support sharing collections between users.
