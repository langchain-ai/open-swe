import "dotenv/config";
import { Client } from "@langchain/langgraph-sdk";
import { v4 as uuidv4 } from "uuid";
import { GraphConfig } from "../src/types.js";
import { graph } from "../src/index.js";
import { createLogger, LogLevel } from "../src/utils/logger.js";

const logger = createLogger(LogLevel.INFO, "From Plan Script");

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
        id: "toolu_01RNEjViZFBXgCSNMwqKvkVL",
        role: "assistant",
        content: [
          {
            index: 0,
            type: "tool_use",
            id: "toolu_01RNEjViZFBXgCSNMwqKvkVL",
            name: "condense_planning_context",
            input:
              '{"context": "The user has a React/Next.js portfolio site with static star counts for GitHub repositories. The project structure is:\\n\\n- Located in ./personal-site/ directory\\n- Uses Next.js 15.2.3 with TypeScript\\n- Already has @vercel/kv dependency installed\\n- Main page at src/app/page.tsx contains hardcoded star values for 6 repositories:\\n  - langchain-ai/langchainjs (14.3k stars)\\n  - langchain-ai/langgraphjs (1.3k stars) \\n  - langchain-ai/open-canvas (4.4k stars)\\n  - langchain-ai/agent-inbox (496 stars)\\n  - langchain-ai/agent-chat-ui (583 stars)\\n  - langchain-ai/social-media-agent (1.2k stars)\\n\\n- Stars are displayed in ProjectCard components with a stars prop\\n- Existing API routes in src/app/api/ (gh-viewer and gh-viewer-v2)\\n- The gh-viewer route already uses Vercel KV for storing view counts\\n\\nThe user wants to:\\n1. Create a daily cron job to fetch real GitHub star counts\\n2. Store the data in Vercel KV\\n3. Update the UI to fetch from KV instead of using static values\\n4. Avoid making GitHub API calls on every page visit\\n\\nThe project already has the necessary infrastructure (Vercel KV, API routes) and just needs the GitHub star fetching logic and UI updates."}',
          },
        ],
        tool_calls: [
          {
            name: "condense_planning_context",
            args: {
              context:
                "The user has a React/Next.js portfolio site with static star counts for GitHub repositories. The project structure is:\n\n- Located in ./personal-site/ directory\n- Uses Next.js 15.2.3 with TypeScript\n- Already has @vercel/kv dependency installed\n- Main page at src/app/page.tsx contains hardcoded star values for 6 repositories:\n  - langchain-ai/langchainjs (14.3k stars)\n  - langchain-ai/langgraphjs (1.3k stars) \n  - langchain-ai/open-canvas (4.4k stars)\n  - langchain-ai/agent-inbox (496 stars)\n  - langchain-ai/agent-chat-ui (583 stars)\n  - langchain-ai/social-media-agent (1.2k stars)\n\n- Stars are displayed in ProjectCard components with a stars prop\n- Existing API routes in src/app/api/ (gh-viewer and gh-viewer-v2)\n- The gh-viewer route already uses Vercel KV for storing view counts\n\nThe user wants to:\n1. Create a daily cron job to fetch real GitHub star counts\n2. Store the data in Vercel KV\n3. Update the UI to fetch from KV instead of using static values\n4. Avoid making GitHub API calls on every page visit\n\nThe project already has the necessary infrastructure (Vercel KV, API routes) and just needs the GitHub star fetching logic and UI updates.",
            },
            id: "toolu_01RNEjViZFBXgCSNMwqKvkVL",
            type: "tool_call",
          },
        ],
        additional_kwargs: {
          summary_message: true,
        },
      },
      {
        role: "tool",
        tool_call_id: "toolu_01RNEjViZFBXgCSNMwqKvkVL",
        name: "condense_planning_context",
        content: "Successfully summarized planning context.",
        additional_kwargs: {
          summary_message: true,
        },
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

  logger.info("Initializing sandbox...");

  const initResult = await graph.nodes.initialize.invoke(inputs as any, {
    configurable,
  });
  if (!initResult.sandboxSessionId) {
    throw new Error("Failed to initialize sandbox.");
  }

  logger.info(
    `Sandbox initialized successfully. ID: ${initResult.sandboxSessionId}\n\n`,
  );

  const stream = client.runs.stream(threadId, "open-swe", {
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
    const node = Object.keys(chunk.data)[0];
    logger.info(`${node} completed.\n`);
  }
}

runFromPlan().catch(logger.error);
