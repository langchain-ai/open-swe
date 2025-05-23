import "dotenv/config";
import { Client } from "@langchain/langgraph-sdk";
import { v4 as uuidv4 } from "uuid";
import { GraphConfig } from "../src/types.js";
import { graph } from "../src/index.js";

async function runFromPlan() {
  const client = new Client({
    apiKey: process.env.LANGCHAIN_API_KEY,
    apiUrl: process.env.LANGGRAPH_API_URL ?? "http://localhost:2024",
  });

  const threadId = uuidv4();

  const inputs = {
    messages: [
      {
        role: "user",
        content:
          "This repo contains the react/next.js code for my persona/portfolio site. It currently has static values set for the number of stars on the repositories I highlight. I want this to be accurate, but I do NOT want it to make requests to GitHub every time a user visits. Instead, please implement a solution which will run once a day, fetch the number of stars from a list of repos, then write them to vercel's KV store. Finally, update the UI to make a request to the KV store when the user visits my page and render the accurate star counts.",
      },
      {
        id: "toolu_01BDCDYr7oGxJnfgY6MjXGnx",
        role: "assistant",
        content: [
          {
            index: 0,
            type: "tool_use",
            id: "toolu_01BDCDYr7oGxJnfgY6MjXGnx",
            name: "condense_planning_context",
            input:
              '{"context": "The user has a React/Next.js portfolio site with static star counts for GitHub repositories. The project structure shows:\\n\\n1. **Current Implementation**: \\n   - Main page at `./personal-site/src/app/page.tsx` contains hardcoded star values for 6 repositories:\\n     - langchain-ai/langchainjs (14.3k stars)\\n     - langchain-ai/langgraphjs (1.3k stars) \\n     - langchain-ai/open-canvas (4.4k stars)\\n     - langchain-ai/agent-inbox (496 stars)\\n     - langchain-ai/agent-chat-ui (583 stars)\\n     - langchain-ai/social-media-agent (1.2k stars)\\n   - Stars are displayed in ProjectCard components with a stars prop\\n\\n2. **Dependencies**: \\n   - Project already has `@vercel/kv` dependency installed\\n   - Uses Next.js 15.2.3 with TypeScript\\n   - Has existing API routes in `./personal-site/src/app/api/` including gh-viewer route that already uses Vercel KV\\n\\n3. **Requirements**:\\n   - Implement daily GitHub API fetching for star counts\\n   - Store results in Vercel KV store  \\n   - Update UI to fetch from KV store instead of static values\\n   - Avoid GitHub API calls on every user visit\\n\\n4. **Repository URLs to track**:\\n   - https://github.com/langchain-ai/langchainjs\\n   - https://github.com/langchain-ai/langgraphjs  \\n   - https://github.com/langchain-ai/open-canvas\\n   - https://github.com/langchain-ai/agent-inbox\\n   - https://github.com/langchain-ai/agent-chat-ui\\n   - https://github.com/langchain-ai/social-media-agent"}',
          },
        ],
        tool_calls: [
          {
            name: "condense_planning_context",
            args: {
              context:
                "The user has a React/Next.js portfolio site with static star counts for GitHub repositories. The project structure shows:\n\n1. **Current Implementation**: \n   - Main page at `./personal-site/src/app/page.tsx` contains hardcoded star values for 6 repositories:\n     - langchain-ai/langchainjs (14.3k stars)\n     - langchain-ai/langgraphjs (1.3k stars) \n     - langchain-ai/open-canvas (4.4k stars)\n     - langchain-ai/agent-inbox (496 stars)\n     - langchain-ai/agent-chat-ui (583 stars)\n     - langchain-ai/social-media-agent (1.2k stars)\n   - Stars are displayed in ProjectCard components with a stars prop\n\n2. **Dependencies**: \n   - Project already has `@vercel/kv` dependency installed\n   - Uses Next.js 15.2.3 with TypeScript\n   - Has existing API routes in `./personal-site/src/app/api/` including gh-viewer route that already uses Vercel KV\n\n3. **Requirements**:\n   - Implement daily GitHub API fetching for star counts\n   - Store results in Vercel KV store  \n   - Update UI to fetch from KV store instead of static values\n   - Avoid GitHub API calls on every user visit\n\n4. **Repository URLs to track**:\n   - https://github.com/langchain-ai/langchainjs\n   - https://github.com/langchain-ai/langgraphjs  \n   - https://github.com/langchain-ai/open-canvas\n   - https://github.com/langchain-ai/agent-inbox\n   - https://github.com/langchain-ai/agent-chat-ui\n   - https://github.com/langchain-ai/social-media-agent",
            },
            id: "toolu_01BDCDYr7oGxJnfgY6MjXGnx",
            type: "tool_call",
          },
        ],
      },
      {
        role: "tool",
        tool_call_id: "toolu_01BDCDYr7oGxJnfgY6MjXGnx",
        name: "condense_planning_context",
        content: "Successfully summarized planning context.",
      },
    ],
    plan: [
      {
        index: 0,
        plan: "Create a list of GitHub repositories with their URLs extracted from the current static star values in page.tsx",
        completed: false,
      },
      {
        index: 1,
        plan: "Create a new API route `/api/github-stars` that fetches star counts from GitHub API for the repository list",
        completed: false,
      },
      {
        index: 2,
        plan: "Create a new API route `/api/stars` that retrieves cached star counts from Vercel KV store",
        completed: false,
      },
      {
        index: 3,
        plan: "Implement a daily cron job using Vercel Cron Jobs (vercel.json) that calls the GitHub stars API and updates the KV store",
        completed: false,
      },
      {
        index: 4,
        plan: "Update the ProjectCard component to fetch star counts from the KV store API instead of using static values",
        completed: false,
      },
      {
        index: 5,
        plan: "Add error handling and fallback to static values if KV store is unavailable",
        completed: false,
      },
      {
        index: 6,
        plan: "Update the page.tsx to remove static star values and implement dynamic fetching",
        completed: false,
      },
    ],
    proposedPlan: [
      "Create a list of GitHub repositories with their URLs extracted from the current static star values in page.tsx",
      "Create a new API route `/api/github-stars` that fetches star counts from GitHub API for the repository list",
      "Create a new API route `/api/stars` that retrieves cached star counts from Vercel KV store",
      "Implement a daily cron job using Vercel Cron Jobs (vercel.json) that calls the GitHub stars API and updates the KV store",
      "Update the ProjectCard component to fetch star counts from the KV store API instead of using static values",
      "Add error handling and fallback to static values if KV store is unavailable",
      "Update the page.tsx to remove static star values and implement dynamic fetching",
    ],
    planChangeRequest: undefined,
    sandboxSessionId: undefined,
    branchName: `open-swe/${threadId}`,
  };

  const configurable: Omit<
    GraphConfig["configurable"],
    "thread_id" | "assistant_id"
  > = {
    target_repository: {
      owner: "bracesproul",
      repo: "personal-site",
    },
  };

  console.log("\nInitializing sandbox...");

  const initResult = await graph.nodes.initialize.invoke(inputs as any, {
    configurable,
  });
  if (!initResult.sandboxSessionId) {
    throw new Error("Failed to initialize sandbox.");
  }

  console.log(
    `Sandbox initialized successfully. ID: ${initResult.sandboxSessionId}\n\n`,
  );

  const stream = client.runs.stream(threadId, "open-codex", {
    command: {
      goto: "generate-action",
      update: {
        ...inputs,
        sandboxSessionId: initResult.sandboxSessionId,
      },
    },
    config: {
      configurable,
      recursion_limit: 200,
    },
    ifNotExists: "create",
    streamSubgraphs: true,
    streamMode: "updates",
  });

  for await (const chunk of stream) {
    console.dir(chunk.data, { depth: null });
  }
}

runFromPlan().catch(console.error);
