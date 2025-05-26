---
title: 'RAG Server Setup'
---

Open Agent Platform has first class support for RAG with your agents. To use this feature, you must deploy your own instance of a LangConnect RAG server.

## About LangConnect

[LangConnect](https://github.com/langchain-ai/langconnect) is an open source managed retrieval service for RAG applications. It's built on top of LangChain's RAG integrations (vectorstores, document loaders, indexing API, etc.) and allows you to quickly spin up an API server for managing your collections & documents for any RAG application.

## Deployment

To set up LangConnect, follow the instructions in the [LangConnect README](https://github.com/langchain-ai/langconnect/blob/main/README.md). After setting it up, and either running the server locally via Docker, or deploying it to a cloud provider, you should set the `NEXT_PUBLIC_RAG_API_URL` environment variable inside the `apps/web/` directory, to the API URL of your LangConnect server.

For local development, this should be set to:

```bash
NEXT_PUBLIC_RAG_API_URL="http://localhost:8080"
```

## Using the RAG Feature

Once this is set, you can visit the `/rag` page in the web app to create your first collection, and upload documents to it.

After setting up your RAG server, and configuring Open Agent Platform with an agent which can call it (like the Tools Agent), you can start to use it in your agents by selecting a collection when creating/editing an agent. This will give the agent access to a tool which can make requests to your RAG server, and retrieve relevant documents for the user's query.

## Best Practices

Since the RAG server is exposed to the agent as a tool, we recommend setting detailed descriptions on your collections when creating them, since these are the descriptions which will be attached to the tool.

## Authentication

As mentioned in the Authentication section, we pass the Supabase JWT in the `Authorization` header of the request. Then, inside the LangConnect RAG server, we extract this token and verify it's valid with Supabase.

If it is, we receive back a user ID, which is used to verify each user is *only* able to access their own collections. We do not currently support sharing collections between users.
