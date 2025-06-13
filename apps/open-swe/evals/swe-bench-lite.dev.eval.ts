// Run evals over the development SWE Bench Lite dataset

import { v4 as uuidv4 } from "uuid";
import * as ls from "langsmith/vitest";
import { Client as LangSmithClient, Example } from "langsmith";
import { Client as LangGraphClient } from "@langchain/langgraph-sdk";
import { SWEBenchInput } from "./swe-bench-types.js";
import { formatInputs } from "./prompts.js";
import { createLogger, LogLevel } from "../src/utils/logger.js";
import { HumanResponse } from "@langchain/langgraph/prebuilt";
import { evaluator } from "./evaluator.js";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { GITHUB_TOKEN_COOKIE } from "@open-swe/shared/constants";

const logger = createLogger(LogLevel.INFO, "Evaluator");

const DATASET_NAME = "swe-bench-lite-dev";

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
  const ex = examples.find((e) => e.inputs.repo.includes("sqlfluff"));
  if (!ex) {
    throw new Error("Example not found");
  }
  return [ex];
}

function convertExampleToInput(example: Example) {
  return {
    inputs: example.inputs as SWEBenchInput,
  };
}

const DATASET = await loadDataset().then((examples) =>
  examples.map(convertExampleToInput),
);

logger.info(`Starting evals over ${DATASET.length} examples...`);

const LANGGRAPH_URL = process.env.LANGGRAPH_URL || "http://localhost:2024";
const GRAPH_NAME = "open_swe";

ls.describe.skip(DATASET_NAME, () => {
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
      logger.info("Starting run", {
        thread_id: threadId,
      });

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
        ...(run as Record<string, any>)["__interrupt__"],
      });
      ls.logOutputs(run);

      // graph interrupted. we should now resume the run, accepting the plan.
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
      ls.logOutputs(resumeRun as Record<string, any>);
      logger.info("Completed run.", {
        thread_id: threadId,
        ...resumeRun,
      });
      logger.info("Starting evaluator...");

      const wrappedEvaluator = ls.wrapEvaluator(evaluator);
      const evalResult = await wrappedEvaluator({
        sweBenchInputs: inputs,
        output: resumeRun as GraphState,
      });
      logger.info("Completed evaluator.", {
        thread_id: threadId,
        evalResult,
      });
    },
    600_000,
  ); // 10 min
});

ls.describe(DATASET_NAME, () => {
  ls.test.each(DATASET)(
    "Can resolve issue",
    async ({ inputs }) => {
      const input = await formatInputs(inputs);

      const wrappedEvaluator = ls.wrapEvaluator(evaluator);
      const evalResult = await wrappedEvaluator({
        sweBenchInputs: inputs,
        output: {
          ...input,
          branchName: "open-swe/e6a73c4e-bfbf-4323-8484-33d14ec25048",
        } as GraphState,
      });
      logger.info("Completed evaluator.", {
        thread_id: "e6a73c4e-bfbf-4323-8484-33d14ec25048",
        evalResult,
      });
    },
    600_000,
  ); // 10 min
});
