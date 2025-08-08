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
import { DATASET } from "./utils/dataset.js";

const logger = createLogger(LogLevel.DEBUG, "Evaluator");

// Configuration constants
const RUN_AGENT_PIPELINE = process.env.RUN_AGENT_PIPELINE === "true" || true;
let programmerRunUrl =
  "https://smith.langchain.com/o/ebbaf2eb-769b-4505-aca2-d11de10372a4/projects/p/eba94921-7f40-4be0-b153-e88ab6fdcfdd/r/";
const DATASET_NAME =
  process.env.DATASET_NAME || "aliyan-open-swe-langgraph-eval";
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

      let branchName: string;

      if (RUN_AGENT_PIPELINE) {
        logger.info("Running full agent pipeline...");

        // Run the agent with user input
        let managerRun;
        try {
          managerRun = await withRetry(() =>
            lgClient.runs.wait(threadId, MANAGER_GRAPH_ID, {
              input,
              config: {
                recursion_limit: 400,
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
          return {
            ruffScore: 0,
            mypyScore: 0,
            langGraphScore: 0,
            details: {
              ruff: { issues: [], error: "Error in manager run" },
              mypy: { issues: [], error: "Error in manager run" },
              langGraph: { explanation: "", error: "Error in manager run" },
            },
          };
        }

        const managerState = managerRun as unknown as ManagerGraphState;
        const plannerSession = managerState?.plannerSession;

        if (!plannerSession) {
          logger.info("Agent did not create a planner session", {
            thread_id: threadId,
          });
          return {
            ruffScore: 0,
            mypyScore: 0,
            langGraphScore: 0,
            details: {
              ruff: {
                issues: [],
                error: "Agent did not create a planner session",
              },
              mypy: {
                issues: [],
                error: "Agent did not create a planner session",
              },
              langGraph: {
                explanation: "",
                error: "Agent did not create a planner session",
              },
            },
          };
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
          return {
            ruffScore: 0,
            mypyScore: 0,
            langGraphScore: 0,
            details: {
              ruff: { issues: [], error: "Error joining planner run" },
              mypy: { issues: [], error: "Error joining planner run" },
              langGraph: {
                explanation: "",
                error: "Error joining planner run",
              },
            },
          };
        }

        // Type-safe access to planner run state
        const plannerState = plannerRun as unknown as PlannerGraphState;
        const programmerSession = plannerState?.programmerSession;

        if (!programmerSession) {
          logger.info("Agent did not create a programmer session", {
            thread_id: threadId,
          });
          return {
            ruffScore: 0,
            mypyScore: 0,
            langGraphScore: 0,
            details: {
              ruff: {
                issues: [],
                error: "Agent did not create a programmer session",
              },
              mypy: {
                issues: [],
                error: "Agent did not create a programmer session",
              },
              langGraph: {
                explanation: "",
                error: "Agent did not create a programmer session",
              },
            },
          };
        }

        let programmerRun;
        programmerRunUrl = `${programmerRunUrl}${programmerSession.runId}`;
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
          return {
            ruffScore: 0,
            mypyScore: 0,
            langGraphScore: 0,
            details: {
              ruff: { issues: [], error: "Error joining programmer run" },
              mypy: { issues: [], error: "Error joining programmer run" },
              langGraph: {
                explanation: "",
                error: "Error joining programmer run",
              },
            },
          };
        }

        const programmerState = programmerRun as unknown as GraphState;
        const agentBranchName = programmerState?.branchName;

        if (!agentBranchName) {
          logger.info("Agent did not create a branch", {
            thread_id: threadId,
          });
          return {
            ruffScore: 0,
            mypyScore: 0,
            langGraphScore: 0,
            details: {
              ruff: { issues: [], error: "Agent did not create a branch" },
              mypy: { issues: [], error: "Agent did not create a branch" },
              langGraph: {
                explanation: "",
                error: "Agent did not create a branch",
              },
            },
          };
        }

        branchName = agentBranchName;
        logger.info("Agent completed. Created branch:", {
          branchName: branchName,
        });
      } else {
        // Skip agent run - evaluate branch directly
        branchName = inputs.branch || "main";
        logger.info("Skipping agent run. Evaluating branch:", {
          branchName: branchName,
        });
      }

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
      return {
        evalResult,
        ...(RUN_AGENT_PIPELINE && { programmerRunUrl }),
      };
    },
    7200_000,
  );
});
