// Run evals over the development Open SWE dataset

import { v4 as uuidv4 } from "uuid";
import * as ls from "langsmith/vitest";
import { Client as LangSmithClient, Example } from "langsmith";
import { Client as LangGraphClient } from "@langchain/langgraph-sdk";
import { OpenSWEInput } from "./open-swe-types.js";
import { formatInputs } from "./prompts.js";
import { createLogger, LogLevel } from "../src/utils/logger.js";
import { HumanResponse } from "@langchain/langgraph/prebuilt";
import { evaluator } from "./evaluator.js";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { GITHUB_TOKEN_COOKIE } from "@open-swe/shared/constants";

const logger = createLogger(LogLevel.INFO, "Evaluator");

const DATASET_NAME = "aliyan-open-swe-langgraph-eval";
const RUN_NAME = `${DATASET_NAME}-${new Date().toISOString().replace(/[:.]/g, '-')}`;

async function loadDataset(): Promise<Example[]> {
  const client = new LangSmithClient();
  const datasetStream = client.listExamples({ datasetName: DATASET_NAME });
  let examples: Example[] = [];
  for await (const example of datasetStream) {
    examples.push(example);
  }
  logger.info(
    `Loaded ${examples.length} examples from dataset "${DATASET_NAME}"`,
  );
  return examples;
}

const DATASET = await loadDataset().then((examples) =>
  examples.map(example => ({
    inputs: example.inputs as OpenSWEInput,
  })),
);

console.log('DATASET', DATASET);

logger.info(`Starting evals over ${DATASET.length} examples...`);

const LANGGRAPH_URL = process.env.LANGGRAPH_URL || "http://localhost:2024";
const GRAPH_NAME = "open_swe";

// FULL PIPELINE: Agent + Evaluation
ls.describe(DATASET_NAME, () => {
  ls.test.each(DATASET)(
    "Can resolve issue",
    async ({ inputs }) => {
      const lgClient = new LangGraphClient({
        apiUrl: LANGGRAPH_URL,
        apiKey: process.env.LANGCHAIN_API_KEY,
        defaultHeaders: {
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

      // 1. AGENT EXECUTION
      const run = await lgClient.runs.wait(threadId, GRAPH_NAME, {
        input,
        config: {
          recursion_limit: 250,
          configurable: {
            [GITHUB_TOKEN_COOKIE]: process.env.GITHUB_PAT,
          },
        },
        ifNotExists: "create",
      });

      if (!("__interrupt__" in run)) {
        throw new Error("Run did not interrupt with initial plan.");
      }

      logger.info("Completed planning step. Accepting plan", {
        thread_id: threadId,
      });

      // Resume agent execution
      const resumeValue: HumanResponse[] = [
        {
          type: "accept",
          args: null,
        },
      ];
      const resumeRun = await lgClient.runs.wait(threadId, GRAPH_NAME, {
        command: {
          resume: resumeValue,
        },
        config: {
          recursion_limit: 250,
        },
      });

      logger.info("Agent completed. Created branch:", {
        branchName: (resumeRun as GraphState).branchName
      });

      // 2. EVALUATION
      const wrappedEvaluator = ls.wrapEvaluator(evaluator);
      const evalResult = await wrappedEvaluator({
        openSWEInputs: inputs,
        output: resumeRun as GraphState,
      });
      
      logger.info("Evaluation completed.", {
        thread_id: threadId,
        evalResult,
      });
    },
    600_000,
  ); // 10 min
});
