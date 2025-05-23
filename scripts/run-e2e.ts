import "dotenv/config";
import { Client } from "@langchain/langgraph-sdk";
import { v4 as uuidv4 } from "uuid";
import { GraphConfig } from "../src/types.js";
import { HumanResponse } from "@langchain/langgraph/prebuilt";

async function runE2E() {
  const client = new Client({
    apiKey: process.env.LANGCHAIN_API_KEY,
    apiUrl: process.env.LANGGRAPH_API_URL ?? "http://localhost:2024",
  });

  const threadId = uuidv4();

  const userRequest = "ADD YOUR REQUEST HERE";

  const configurable: Omit<
    GraphConfig["configurable"],
    "thread_id" | "assistant_id"
  > = {
    target_repository: {
      owner: "YOUR_USERNAME",
      repo: "YOUR_REPO",
      branch: "OPTIONAL BRANCH NAME",
    },
  };

  const stream = client.runs.stream(threadId, "open-codex", {
    input: {
      messages: [{ role: "user", content: userRequest }],
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

async function resumeGraph(threadId: string) {
  const client = new Client({
    apiKey: process.env.LANGCHAIN_API_KEY,
    apiUrl: process.env.LANGGRAPH_API_URL ?? "http://localhost:2024",
  });

  // EDIT THIS IF YOU DO NOT WANT TO ACCEPT
  const resumeValue: HumanResponse[] = [
    {
      type: "accept",
      args: null,
    },
  ];

  const stream = client.runs.stream(threadId, "open-codex", {
    command: {
      resume: resumeValue,
    },
    streamSubgraphs: true,
    streamMode: "updates",
  });

  for await (const chunk of stream) {
    console.dir(chunk.data, { depth: null });
  }
}

const args = process.argv.slice(2); // Skip node executable and script path

if (args.length === 0) {
  runE2E().catch((error) => {
    console.error("Error running E2E test:", error);
    process.exit(1);
  });
} else if (args.length === 2 && args[0] === "--threadId") {
  const threadId = args[1];
  resumeGraph(threadId).catch((error) => {
    console.error(`Error resuming graph for thread ID ${threadId}:`, error);
    process.exit(1);
  });
} else {
  console.log("Usage:");
  console.log("  To run a new E2E test:");
  console.log("    yarn run:e2e");
  console.log("");
  console.log("  To resume a graph with a thread ID:");
  console.log("    yarn run:e2e --threadId <thread_id>");
  process.exit(1);
}
