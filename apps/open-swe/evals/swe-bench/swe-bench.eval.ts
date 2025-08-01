// Run SWE-bench evaluations over the SWE-bench dataset

import { v4 as uuidv4 } from "uuid";
import * as ls from "langsmith/vitest";
import { createLogger, LogLevel } from "../../src/utils/logger.js";
import { sweBenchEvaluator } from "./evaluator.js";
import { SWEBenchInput } from "./types.js";
import { MANAGER_GRAPH_ID, GITHUB_PAT } from "@open-swe/shared/constants";
import { createLangGraphClient } from "../../src/utils/langgraph-client.js";
import { encryptSecret } from "@open-swe/shared/crypto";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { withRetry } from "../utils/retry.js";
import { formatInputs } from "../prompts.js";

const logger = createLogger(LogLevel.DEBUG, "SWEBenchEvaluator");

// Environment variables for configuration
const DATASET_NAME =
  process.env.SWE_BENCH_DATASET_NAME || "princeton-nlp/SWE-bench_Lite";
const INSTANCE_IDS = process.env.SWE_BENCH_INSTANCE_IDS?.split(",") || [];
const MAX_WORKERS = parseInt(process.env.SWE_BENCH_MAX_WORKERS || "1");
const TIMEOUT_MINUTES = parseInt(
  process.env.SWE_BENCH_TIMEOUT_MINUTES || "120",
);
const CACHE_LEVEL = process.env.SWE_BENCH_CACHE_LEVEL || "env";
const CLEANUP = process.env.SWE_BENCH_CLEANUP !== "false"; // Default true

// Example SWE-bench Lite instances for testing
// In production, these would be loaded from the actual dataset
const EXAMPLE_SWE_BENCH_INSTANCES: SWEBenchInput[] = [
  {
    instance_id: "sympy__sympy-20590",
    repo: "sympy/sympy",
    problem_statement: `Symbol instances have __dict__ since 1.7?

In version 1.6.2 Symbol instances had no __dict__, but in 1.7 they seem to have one:

Python 3.6.9 (default, Nov  7 2019, 10:44:02) 
[GCC 8.3.0] on linux
Type "help", "copyright", "credits" or "license" for more information.
>>> import sympy
>>> sympy.__version__
'1.6.2'
>>> x = sympy.Symbol('x')
>>> hasattr(x, '__dict__')
False

vs

Python 3.6.9 (default, Nov  7 2019, 10:44:02) 
[GCC 8.3.0] on linux
Type "help", "copyright", "credits" or "license" for more information.
>>> import sympy
>>> sympy.__version__
'1.7'
>>> x = sympy.Symbol('x')
>>> hasattr(x, '__dict__')
True
>>> x.__dict__
{}

This causes problems when pickling Symbol instances with protocol 2 and loading in 1.6.2. I think this happens because the pickling assumes that the instance will have a __dict__ attribute when it doesn't.`,
    base_commit: "3ac1464b8840d5f8b618a654f9fbf09c452fe969",
    FAIL_TO_PASS: ["test_symbol_pickle_protocol_2"],
    PASS_TO_PASS: ["test_symbol_basic", "test_symbol_assumptions"],
  },
  {
    instance_id: "django__django-13658",
    repo: "django/django",
    problem_statement: `ManagementUtility instantiates CommandParser without passing already-computed prog argument

ManagementUtility.execute() computes the prog argument that should be passed to the CommandParser constructor. However, when calling create_parser(), it does not pass this argument. This results in all Django management commands using os.path.basename(sys.argv[0]) as the program name in their help text, which can be incorrect in some scenarios.

The fix is simple: pass the already-computed prog_name to create_parser().`,
    base_commit: "f997b5e6ae85e3df51c51b7a3e1c9d7790f69041",
    FAIL_TO_PASS: ["test_management_utility_prog_name"],
    PASS_TO_PASS: ["test_management_utility_basic"],
  },
];

// Load dataset based on configuration
async function loadDataset(): Promise<SWEBenchInput[]> {
  // In a real implementation, this would load from Hugging Face or local files
  // For now, we'll use example instances
  logger.info(`Loading SWE-bench dataset: ${DATASET_NAME}`);

  let instances = EXAMPLE_SWE_BENCH_INSTANCES;

  // Filter by instance IDs if specified
  if (INSTANCE_IDS.length > 0) {
    instances = instances.filter((inst) =>
      INSTANCE_IDS.includes(inst.instance_id),
    );
    logger.info(`Filtered to ${instances.length} instances by ID`);
  }

  return instances;
}

// Format the problem statement for the agent
async function formatProblemStatement(input: SWEBenchInput): Promise<string> {
  const formatted = await formatInputs({
    user_input: input.problem_statement,
    repo: input.repo,
    branch: "main", // SWE-bench typically uses main branch
  });
  return (
    (formatted.messages?.[0]?.content as string) || input.problem_statement
  );
}

// Main dataset for evaluation
const DATASET = await loadDataset().then((instances) =>
  instances.map((instance) => ({
    inputs: instance,
  })),
);

// Run evaluation for each instance
ls.describe("SWE-bench Evaluation", () => {
  ls.test.each(DATASET)(
    "SWE-bench: ${inputs.instance_id}",
    async ({ inputs }) => {
      const threadId = uuidv4();

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

      logger.info("Starting SWE-bench evaluation", {
        instance_id: inputs.instance_id,
        repo: inputs.repo,
        thread_id: threadId,
      });

      // Format the problem for the agent
      const formattedProblem = await formatProblemStatement(inputs);

      // Run the manager graph
      logger.info("Running manager graph...", {
        thread_id: threadId,
      });

      let managerRun: any;
      try {
        managerRun = await withRetry(() =>
          lgClient.runs.wait(threadId, MANAGER_GRAPH_ID, {
            input: {
              messages: [
                {
                  role: "user",
                  content: formattedProblem,
                },
              ],
            },
            config: {
              recursion_limit: 250,
            },
            ifNotExists: "create",
          }),
        );

        await withRetry(() => lgClient.runs.join(threadId, managerRun.run_id));
      } catch (error) {
        logger.error("Manager run failed", {
          thread_id: threadId,
          error:
            error instanceof Error
              ? {
                  message: error.message,
                  stack: error.stack,
                  name: error.name,
                }
              : error,
        });
        return; // Award 0 points
      }

      // Get the manager state to find planner session
      const managerState = managerRun as unknown as ManagerGraphState;
      const plannerSession = managerState?.plannerSession;

      if (!plannerSession) {
        logger.info("Agent did not create a planner session", {
          thread_id: threadId,
        });
        return; // Award 0 points
      }

      // Join the planner run
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
                }
              : error,
        });
        return; // Award 0 points
      }

      // Get programmer session from planner state
      const plannerState = plannerRun as unknown as PlannerGraphState;
      const programmerSession = plannerState?.programmerSession;

      if (!programmerSession) {
        logger.info("Agent did not create a programmer session", {
          thread_id: threadId,
        });
        return; // Award 0 points
      }

      // Join the programmer run to get final state
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
        return; // Award 0 points
      }

      const programmerState = programmerRun as unknown as GraphState;
      const branchName = programmerState?.branchName;

      if (!branchName) {
        logger.info("Agent did not create a branch", {
          thread_id: threadId,
        });
        return; // Award 0 points
      }

      logger.info("Agent completed. Created branch:", {
        branchName: branchName,
      });

      // Extract repository information from the input
      const [owner, repo] = inputs.repo.split("/");
      const targetRepository = {
        owner,
        repo,
      };

      // Run SWE-bench evaluation
      const wrappedEvaluator = ls.wrapEvaluator(sweBenchEvaluator);
      const evalResult = await wrappedEvaluator({
        sweBenchInput: inputs,
        agentOutput: {
          branchName,
          targetRepository,
        },
        githubToken: process.env.GITHUB_PAT,
        modelName: "open-swe-agent",
        harnessOptions: {
          dataset_name: DATASET_NAME,
          max_workers: MAX_WORKERS,
          cache_level: CACHE_LEVEL as any,
          timeout: 1800, // 30 minutes per instance
          clean: CLEANUP,
        },
        cleanup: CLEANUP,
        baseDir: "./swe-bench-evals",
      });

      logger.info("SWE-bench evaluation completed.", {
        thread_id: threadId,
        instance_id: inputs.instance_id,
        evalResult,
      });
    },
    TIMEOUT_MINUTES * 60 * 1000, // Convert to milliseconds
  );
});
