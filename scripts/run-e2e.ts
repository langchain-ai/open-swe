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

  const userRequest =
    "This repo contains the react/next.js code for my persona/portfolio site. It currently has static values set for the number of stars on the repositories I highlight. I want this to be accurate, but I do NOT want it to make requests to GitHub every time a user visits. Instead, please implement a solution which will run once a day, fetch the number of stars from a list of repos, then write them to vercel's KV store. Finally, update the UI to make a request to the KV store when the user visits my page and render the accurate star counts.";

  const configurable: Omit<
    GraphConfig["configurable"],
    "thread_id" | "assistant_id"
  > = {
    target_repository: {
      owner: "bracesproul",
      repo: "personal-site",
    },
  };

  const stream = client.runs.stream(threadId, "open-swe", {
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

  console.log(`\nRun started with thread ID: "${threadId}"\n`);

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

  const stream = client.runs.stream(threadId, "open-swe", {
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
