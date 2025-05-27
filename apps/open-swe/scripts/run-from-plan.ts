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
        id: "msg_01UM7mQS4P37haW1LrABGZez",
        role: "assistant",
        content:
          "The project is structured as a monorepo, with some apps located inside the /apps directory. In this directory, there is an /auth directory. This directory only contains the scaffolding for a new app in the monorepo, but is not yet implemented. Please take the following plan/task description and implement it in the /auth directory:\nThis monorepo is for an AI coding agent. The app runs and edits the code in the cloud in a sandboxed environment. Right now, we require users to generate a GitHub PAT, which we store in a .env file and can use to authenticate with GitHub. This is not idea, and instead we want to have a github oauth app which users can authenticate with.\nPlease implement a new auth server inside the /auth directory which can do this.\nYou will not have any access to secrets, so you will not be able to run the server to test it.\nI want the server to be able to authenticate users with GitHub, such that we will be able to take the following actions:\n1. clone repositories they give us access to\n2. checkout existing and create new branches on the repositories they give us access to\n3. make pull requests and push changes to the repositories they give us access to\nOnce you're done, ensure you've documented the development process in the readme of this new app.",
        additional_kwargs: {
          summary_message: true,
        },
      },
    ],
    plan: [
      {
        index: 0,
        plan: "Set up the Express.js server with TypeScript configuration and necessary dependencies for GitHub OAuth authentication",
        completed: false,
      },
      {
        index: 1,
        plan: "Implement GitHub OAuth flow endpoints including authorization redirect and callback handling",
        completed: false,
      },
      {
        index: 2,
        plan: "Create middleware for JWT token generation and validation for authenticated sessions",
        completed: false,
      },
      {
        index: 3,
        plan: "Add environment configuration management for OAuth app credentials and server settings",
        completed: false,
      },
      {
        index: 4,
        plan: "Create user session management and token storage mechanisms",
        completed: false,
      },
      {
        index: 5,
        plan: "Implement API endpoints for checking authentication status and user permissions",
        completed: false,
      },
      {
        index: 6,
        plan: "Add comprehensive error handling and logging throughout the authentication flow",
        completed: false,
      },
      {
        index: 7,
        plan: "Create comprehensive README documentation covering setup, configuration, and development process",
        completed: false,
      },
      {
        index: 8,
        plan: "Add TypeScript type definitions for GitHub OAuth responses and internal data structures",
        completed: false,
      },
    ],
    proposedPlan: [
      "Set up the Express.js server with TypeScript configuration and necessary dependencies for GitHub OAuth authentication",
      "Implement GitHub OAuth flow endpoints including authorization redirect and callback handling",
      "Create middleware for JWT token generation and validation for authenticated sessions",
      "Add environment configuration management for OAuth app credentials and server settings",
      "Create user session management and token storage mechanisms",
      "Implement API endpoints for checking authentication status and user permissions",
      "Add comprehensive error handling and logging throughout the authentication flow",
      "Create comprehensive README documentation covering setup, configuration, and development process",
      "Add TypeScript type definitions for GitHub OAuth responses and internal data structures",
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
      owner: "langchain-ai",
      repo: "open-swe",
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
      recursion_limit: 400,
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
