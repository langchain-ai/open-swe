// Run evals over the development Open SWE dataset

import { v4 as uuidv4 } from "uuid";
import * as ls from "langsmith/vitest";
import { formatInputs } from "./prompts.js";
import { createLogger, LogLevel } from "../src/utils/logger.js";
import { evaluator } from "./evaluator.js";
import { MANAGER_GRAPH_ID, GITHUB_PAT } from "@open-swe/shared/constants";
import { createLangGraphClient } from "../src/utils/langgraph-client.js";
import { encryptSecret } from "@open-swe/shared/crypto";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { withRetry } from "./utils/retry.js";

const logger = createLogger(LogLevel.DEBUG, "Evaluator");

const DATASET_NAME = process.env.DATASET_NAME || "";
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
      repo: "mai-sandbox/open-swe_content_team_eval",
      branch: "main",
      user_input: `I have implemented a multi-agent content creation system using LangGraph that orchestrates collaboration between specialized agents. The system is experiencing multiple runtime errors and workflow failures that prevent proper execution.

System Architecture
The application implements a three-agent architecture:

Research Agent: Utilizes web search tools to gather information on specified topics
Writer Agent: Creates content based on research findings with creative temperature settings
Reviewer Agent: Provides feedback using fact-checking tools and determines revision needs

Expected Workflow
User Request → Research Agent → Writer Agent → Reviewer Agent → [Revision Loop if needed] → Final Content

Current Issues

Runtime Errors: Application fails to start with import and graph compilation errors
Agent Handoff Failures: Agents are not properly transferring control and context
Tool Integration Problems: Tool calling mechanisms are not functioning correctly
State Management Issues: Shared state is not being updated correctly across agent transitions
Routing Logic Failures: Conditional edges and workflow routing are broken`,
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

      const encryptionKey = process.env.SECRETS_ENCRYPTION_KEY;
      const githubPat = process.env.GITHUB_PAT;

      if (!encryptionKey || !githubPat) {
        throw new Error(
          "SECRETS_ENCRYPTION_KEY and GITHUB_PAT environment variables are required",
        );
      }

      const encryptedGitHubToken = encryptSecret(githubPat, encryptionKey);

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
      let managerRun;
      try {
        managerRun = await withRetry(() =>
          lgClient.runs.wait(threadId, MANAGER_GRAPH_ID, {
            input,
            config: {
              recursion_limit: 250,
            },
            ifNotExists: "create",
          }),
        );
      } catch (error) {
        logger.error("Error in manager run", {
          thread_id: threadId,
          error:
            error instanceof Error
              ? {
                  message: error.message,
                  stack: error.stack,
                  name: error.name,
                  cause: error.cause,
                }
              : error,
        });
        return; // instead of skipping, we should award 0 points
      }

      const managerState = managerRun as unknown as ManagerGraphState;
      const plannerSession = managerState?.plannerSession;

      if (!plannerSession) {
        logger.info("Agent did not create a planner session", {
          thread_id: threadId,
        });
        return; // instead of skipping, we should award 0 points
      }

      let plannerRun;
      try {
        plannerRun = await withRetry(() =>
          lgClient.runs.join(plannerSession.threadId, plannerSession.runId),
        );
      } catch (error) {
        logger.error("Error joining planner run", {
          thread_id: threadId,
          plannerSession,
          error:
            error instanceof Error
              ? {
                  message: error.message,
                  stack: error.stack,
                  name: error.name,
                  cause: error.cause,
                }
              : error,
        });
        return; // instead of skipping, we should award 0 points
      }

      // Type-safe access to planner run state
      const plannerState = plannerRun as unknown as PlannerGraphState;
      const programmerSession = plannerState?.programmerSession;

      if (!programmerSession) {
        logger.info("Agent did not create a programmer session", {
          thread_id: threadId,
        });
        return; // instead of skipping, we should award 0 points
      }

      let programmerRun;
      try {
        programmerRun = await withRetry(() =>
          lgClient.runs.join(
            programmerSession.threadId,
            programmerSession.runId,
          ),
        );
      } catch (error) {
        logger.error("Error joining programmer run", {
          thread_id: threadId,
          programmerSession,
          error:
            error instanceof Error
              ? {
                  message: error.message,
                  stack: error.stack,
                  name: error.name,
                  cause: error.cause,
                }
              : error,
        });
        return; // instead of skipping, we should award 0 points
      }

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
    7200_000,
  );
});
