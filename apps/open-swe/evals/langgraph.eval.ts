// Run evals over the development Open SWE dataset

import { v4 as uuidv4 } from "uuid";
import * as ls from "langsmith/vitest";
import { formatInputs } from "./prompts.js";
import { createLogger, LogLevel } from "../src/utils/logger.js";
import { evaluator } from "./evaluator.js";
import { MANAGER_GRAPH_ID, GITHUB_PAT } from "@open-swe/shared/constants";
import { createLangGraphClient } from "../src/utils/langgraph-client.js";
import { encryptGitHubToken } from "@open-swe/shared/crypto";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
import { GraphState } from "@open-swe/shared/open-swe/types";

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

const DATASET = [
  {
    inputs: {
      repo: "mai-sandbox/open-swe_edit_task_1a",
      branch: "main",
      user_input:
        "I have a LangGraph React agent that I want to enhance with web search capabilities. Please add Tavily search functionality to this agent so it can search the web for current information and provide up-to-date responses.\n\nRequirements:\n\n- Add Tavily search tool integration\n- Configure it to return 3 results with advanced search depth\n- Use environment variables for API keys (assume they will be in .env)\n- Maintain the existing conversation memory functionality",
    },
  },
];

logger.info(`Starting evals over ${DATASET.length} examples...`);

//const LANGGRAPH_URL = process.env.LANGGRAPH_URL || "http://localhost:2024";

ls.describe(DATASET_NAME, () => {
  ls.test.each(DATASET)(
    "Can resolve issue",
    async ({ inputs }) => {
      logger.info("Starting agent run", {
        inputs,
      });

      const encryptionKey = process.env.GITHUB_TOKEN_ENCRYPTION_KEY;
      const githubPat = process.env.GITHUB_PAT;

      if (!encryptionKey || !githubPat) {
        throw new Error(
          "GITHUB_TOKEN_ENCRYPTION_KEY and GITHUB_PAT environment variables are required",
        );
      }

      const encryptedGitHubToken = encryptGitHubToken(githubPat, encryptionKey);

      const lgClient = createLangGraphClient({
        includeApiKey: true,
        defaultHeaders: { [GITHUB_PAT]: encryptedGitHubToken },
      });

      const input = await formatInputs(inputs);

      const threadId = uuidv4();
      logger.info("Starting agent run", {
        thread_id: threadId,
        problem: inputs.user_input,
        repo: inputs.repo,
      });

      // Run the agent with user input
      const managerRun = await lgClient.runs.wait(threadId, MANAGER_GRAPH_ID, {
        input,
        config: {
          recursion_limit: 250,
        },
        ifNotExists: "create",
      });

      const managerState = managerRun as unknown as ManagerGraphState;
      const plannerSession = managerState?.plannerSession;

      if (!plannerSession) {
        logger.info("Agent did not create a planner session", {
          thread_id: threadId,
        });
        return; // instead of skipping, we should award 0 points
      }

      const plannerRun = await lgClient.runs.join(
        plannerSession.threadId,
        plannerSession.runId,
      );

      // Type-safe access to planner run state
      const plannerState = plannerRun as unknown as PlannerGraphState;
      const programmerSession = plannerState?.programmerSession;

      if (!programmerSession) {
        logger.info("Agent did not create a programmer session", {
          thread_id: threadId,
        });
        return; // instead of skipping, we should award 0 points
      }

      const programmerRun = await lgClient.runs.join(
        programmerSession.threadId,
        programmerSession.runId,
      );

      // Type-safe access to programmer run state
      const programmerState = programmerRun as unknown as GraphState;
      const branchName = programmerState?.branchName;

      if (!branchName) {
        logger.info("Agent did not create a branch", {
          thread_id: threadId,
        });
        return; // instead of skipping, we should award 0 points
      }

      logger.info("Agent completed. Created branch:", {
        branchName: branchName,
      });

      // Evaluation
      const wrappedEvaluator = ls.wrapEvaluator(evaluator);
      const evalResult = await wrappedEvaluator({
        openSWEInputs: inputs,
        output: {
          branchName,
          targetRepository: {
            owner: inputs.repo.split("/")[0],
            repo: inputs.repo.split("/")[1],
          },
        },
      });

      logger.info("Evaluation completed.", {
        thread_id: threadId,
        evalResult,
      });
    },
    900_000,
  );
});
