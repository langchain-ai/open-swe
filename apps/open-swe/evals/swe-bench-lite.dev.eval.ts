// Run evals over the development Open SWE dataset

import { v4 as uuidv4 } from "uuid";
import * as ls from "langsmith/vitest";
// import { Client as LangSmithClient, Example } from "langsmith";
import { Client as LangGraphClient } from "@langchain/langgraph-sdk";
// import { OpenSWEInput } from "./open-swe-types.js";
import { formatInputs } from "./prompts.js";
import { createLogger, LogLevel } from "../src/utils/logger.js";
import { HumanResponse } from "@langchain/langgraph/prebuilt";
import { evaluator } from "./evaluator.js";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { GITHUB_TOKEN_COOKIE, MANAGER_GRAPH_ID } from "@open-swe/shared/constants";

const logger = createLogger(LogLevel.INFO, "Evaluator");

const DATASET_NAME = "aliyan-open-swe-langgraph-eval-2";
// const RUN_NAME = `${DATASET_NAME}-${new Date().toISOString().replace(/[:.]/g, '-')}`;

// async function loadDataset(): Promise<Example[]> {
//   const client = new LangSmithClient();
//   const datasetStream = client.listExamples({ datasetName: DATASET_NAME });
//   let examples: Example[] = [];
//   for await (const example of datasetStream) {
//     examples.push(example);
//   }
//   logger.info(
//     `Loaded ${examples.length} examples from dataset "${DATASET_NAME}"`,
//   );
//   return examples;
// }

// const DATASET = await loadDataset().then((examples) =>
//   examples.map(example => ({
//     inputs: example.inputs as OpenSWEInput,
//   })),
// );

const DATASET = [{
  inputs: {
    "repo": "mai-sandbox/open-swe_chatbot_eval",
    "branch": "bug-fix-with-langCode-tool",
    "user_input": "just fix any minor error that's too obvious"
  }
}]

console.log('DATASET', DATASET);

logger.info(`Starting evals over ${DATASET.length} examples...`);

const LANGGRAPH_URL = process.env.LANGGRAPH_URL || "http://localhost:2024";

// FULL PIPELINE: Agent + Evaluation
ls.describe(DATASET_NAME, () => {
  ls.test.each(DATASET)(
    "Can resolve issue",
    async ({ inputs }) => {
      logger.info("Starting agent run", {
        inputs,
      });
      const lgClient = new LangGraphClient({
        apiUrl: LANGGRAPH_URL,
        apiKey: process.env.LANGCHAIN_API_KEY,
        defaultHeaders: {
          // TODO: encrypt this before sending
          [GITHUB_TOKEN_COOKIE]: process.env.GITHUB_PAT,
        },
      });

      logger.info("Constructing input");
      const input = await formatInputs(inputs);

      const threadId = uuidv4();
      logger.info("Starting agent run", {
        thread_id: threadId,
        problem: inputs.user_input,
        repo: inputs.repo
      });

      // 1. Run the agent
      await lgClient.runs.wait(threadId, MANAGER_GRAPH_ID, {
        input,
        config: {
          recursion_limit: 250,
        },
        ifNotExists: "create",
      });
      const branchName = `open-swe/${threadId}`

      logger.info("Agent completed. Created branch:", {
        branchName,
      });

      // 2. EVALUATION
      const wrappedEvaluator = ls.wrapEvaluator(evaluator);
      const evalResult = await wrappedEvaluator({
        openSWEInputs: inputs,
        output: {
          branchName,
          targetRepository: {
            owner: inputs.repo.split("/")[0],
            repo: inputs.repo.split("/")[1],
          }
        }
      });
      
      logger.info("Evaluation completed.", {
        thread_id: threadId,
        evalResult,
      });
    },
    600_000,
  ); // 10 min
});
