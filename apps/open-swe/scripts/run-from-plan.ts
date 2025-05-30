import "dotenv/config";
import { Client } from "@langchain/langgraph-sdk";
import { v4 as uuidv4 } from "uuid";
import { graph } from "../src/index.js";
import { createLogger, LogLevel } from "../src/utils/logger.js";

const logger = createLogger(LogLevel.INFO, "From Plan Script");

async function runFromPlan() {
  const client = new Client({
    apiKey: process.env.LANGCHAIN_API_KEY,
    apiUrl: process.env.LANGGRAPH_API_URL ?? "http://localhost:2024",
  });

  const threadId = uuidv4();

  const targetRepository = {
    owner: "langchain-ai",
    repo: "open-swe",
  };

  const inputs = {
    targetRepository,
    messages: [
      {
        role: "user",
        content: `The project is structured as a monorepo, with some apps located inside the /apps directory. In this directory, there is an /auth directory. This directory only contains the scaffolding for a new app in the monorepo, but is not yet implemented. Please take the following plan/task description and implement it in the /auth directory:
This monorepo is for an AI coding agent. The app runs and edits the code in the cloud in a sandboxed environment. Right now, we require users to generate a GitHub PAT, which we store in a .env file and can use to authenticate with GitHub. This is not idea, and instead we want to have a github oauth app which users can authenticate with.
Please implement a new auth server inside the /auth directory which can do this.
You will not have any access to secrets, so you will not be able to run the server to test it.
I want the server to be able to authenticate users with GitHub, such that we will be able to take the following actions:
1. clone repositories they give us access to
2. checkout existing and create new branches on the repositories they give us access to
3. make pull requests and push changes to the repositories they give us access to
Once you're done, ensure you've documented the development process in the readme of this new app.`,
      },
    ],
    plan: [
      {
        index: 0,
        plan: "Set up the Express.js server with TypeScript configuration and necessary dependencies for GitHub OAuth authentication",
        completed: false,
        summary: undefined,
      },
      {
        index: 1,
        plan: "Implement GitHub OAuth flow endpoints including authorization redirect and callback handling",
        completed: false,
        summary: undefined,
      },
      {
        index: 2,
        plan: "Create middleware for JWT token generation and validation for authenticated sessions",
        completed: false,
        summary: undefined,
      },
      {
        index: 3,
        plan: "Add environment configuration management for OAuth app credentials and server settings",
        completed: false,
        summary: undefined,
      },
      {
        index: 4,
        plan: "Add comprehensive error handling throughout the authentication flow",
        completed: false,
        summary: undefined,
      },
      {
        index: 5,
        plan: "Create comprehensive README documentation covering setup, configuration, and development process",
        completed: false,
        summary: undefined,
      },
    ],
    proposedPlan: [
      "Set up the Express.js server with TypeScript configuration and necessary dependencies for GitHub OAuth authentication",
      "Implement GitHub OAuth flow endpoints including authorization redirect and callback handling",
      "Create middleware for JWT token generation and validation for authenticated sessions",
      "Add environment configuration management for OAuth app credentials and server settings",
      "Add comprehensive error handling throughout the authentication flow",
      "Create comprehensive README documentation covering setup, configuration, and development process",
    ],
    planContextSummary: `## User Request Summary
The user wants to implement a GitHub OAuth authentication server in the \`/apps/auth\` directory of a monorepo for an AI coding agent. The goal is to replace the current GitHub PAT authentication system with OAuth to enable:
1. Cloning repositories users give access to
2. Checking out existing and creating new branches
3. Making pull requests and pushing changes

## Codebase Files and Descriptions
- **Project root**: \`/home/user/open-swe/\` - Main monorepo directory
- **Apps directory**: \`/home/user/open-swe/apps/\` - Contains multiple apps including auth, docs, and open-swe
- **Auth app directory**: \`/home/user/open-swe/apps/auth/\` - Target directory for implementation, currently contains only scaffolding
- **Auth package.json**: \`/home/user/open-swe/apps/auth/package.json\` - Contains basic TypeScript/Node.js setup with name "@open-swe/auth", includes dev dependencies for TypeScript, Jest, ESLint, Prettier
- **Auth src directory**: \`/home/user/open-swe/apps/auth/src/\` - Contains only an empty \`index.ts\` file
- **Auth config files**: Directory includes standard config files (.gitignore, .dockerignore, .prettierrc, eslint.config.js, jest.config.js, tsconfig.json, turbo.json)

## Key Repository Insights and Learnings
- The monorepo uses Yarn as package manager (version 3.5.1)
- TypeScript is used throughout with version ~5.7.2
- The auth app is set up as an ES module (type: "module" in package.json)
- Standard tooling includes ESLint, Prettier, Jest for testing
- The auth directory is completely empty except for scaffolding - no existing implementation
- The project appears to be part of the LangChain AI organization based on repository URL
- No access to secrets/environment variables for testing
- Need to document the development process in a README for the auth app

## Implementation Requirements
- Implement GitHub OAuth flow for authentication
- Ensure the server can handle repository operations (clone, branch management, PR creation)
- Create comprehensive documentation in README
- Follow existing monorepo patterns and tooling setup`,
    codebaseContext: "",
    planChangeRequest: undefined,
    sandboxSessionId: undefined,
    branchName: `open-swe/${threadId}`,
  };

  logger.info("Initializing sandbox...");

  const initResult = await graph.nodes.initialize.invoke(inputs as any);
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
