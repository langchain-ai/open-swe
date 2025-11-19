# Feature Graph Generation Flow

The chat interface lets a user trigger feature graph generation for a thread. When the UI calls `generateGraph`, it posts the thread ID and prompt to the Next.js API route at `/api/feature-graph/generate`. That route validates the request, loads the manager thread state to find the workspace path and configurable metadata, and then forwards the prompt and context to the backend service.

The backend service receives the request at `/feature-graph/generate`, resolves the workspace path inside the sandbox, and constructs a `GraphConfig` containing the workspace path plus any configurable fields from the manager state. It calls `generateFeatureGraphForWorkspace`, which loads the planner model and asks it (via an LLM) to create a feature graph JSON based on the repository structure, README excerpt, and the user prompt. The resulting graph file is persisted to `features/graph/graph.yaml` and returned to the web app along with the active feature IDs.

Overall, the chat input is passed through the API layers into an LLM-backed generator that produces the feature nodes and edges added to the graph. The generated graph is saved to disk and stored in thread state so the UI can render it.
