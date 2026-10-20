import { defineMemory, memoryLayer } from "managed-deepagents";

// Durable memory for this deployment.
//
// Memory is opt-in: without this file the agent keeps nothing between runs.
//
// Enable agent and user memory independently with
// `agent: memoryLayer()` and `user: memoryLayer()`.
// Omitted layers are disabled. Agent memory is shared across callers at
// `/memories/agent/`. User memory requires identity and defaults to managed
// Slack one-to-one DMs at `/memories/user/`. Either layer can provide an
// `allow(context)` policy. Each tree is read/write; its `AGENTS.md` is hot
// memory and other files are read on demand.
//
// The agent layer is a trust boundary. Because runs can write hot memory and hot
// memory is injected into every later run, whatever one caller gets the agent to
// save is read by everyone after them — instructions included. Treat memory as
// untrusted input, keep authority (tool access, approvals) in agent.ts rather
// than in memory, and mount no agent layer if your callers should not influence
// each other.
//
// The content lives in LangSmith Context Hub, not in this file: deploys never
// overwrite what earlier runs learned.
export const memory = defineMemory(__MDA_MEMORY_LAYERS__);
