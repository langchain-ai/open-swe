import * as ls from "langsmith/vitest";
import dotenv from "dotenv";
import { Daytona, Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "../src/utils/logger.js";
import { DEFAULT_SANDBOX_CREATE_PARAMS } from "../src/constants.js";
import { readFileSync } from "fs";
import { cloneRepo, checkoutFilesFromCommit } from "../src/utils/github/git.js";
import { GraphState, TargetRepository } from "@open-swe/shared/open-swe/types";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { setupEnv } from "../src/utils/env-setup.js";
import { PRData, PRProcessResult, OpenSWEStreamResults } from "./types.js";
import { createPRFixPrompt } from "./prompts.js";
import { runPytestOnFiles } from "./utils.js";
import { v4 as uuidv4 } from "uuid";
import { MANAGER_GRAPH_ID, GITHUB_PAT, GITHUB_INSTALLATION_ID } from "@open-swe/shared/constants";
import { createLangGraphClient } from "../src/utils/langgraph-client.js";
import { encryptSecret } from "@open-swe/shared/crypto";
import { ManagerGraphState } from "@open-swe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@open-swe/shared/open-swe/planner/types";
import { withRetry } from "../src/utils/retry.js";

dotenv.config();

const logger = createLogger(LogLevel.INFO, "PR Processor");

// Load PRs data and transform snake_case to camelCase
const rawPrsData = JSON.parse(
  readFileSync("langbench/static/langgraph_prs.json", "utf8"),
);

const prsData: PRData[] = rawPrsData.map((pr: any) => ({
  url: pr.url,
  htmlUrl: pr.html_url,
  diffUrl: pr.diff_url,
  patchUrl: pr.patch_url,
  repoOwner: pr.repo_owner,
  repoName: pr.repo_name,
  prNumber: pr.pr_number,
  mergeCommitSha: pr.merge_commit_sha,
  preMergeCommitSha: pr.pre_merge_commit_sha,
  title: pr.title,
  body: pr.body,
  createdAt: pr.created_at,
  mergedAt: pr.merged_at,
  testFiles: pr.test_files || [],
}));

const DATASET = prsData.map((pr) => ({ inputs: pr }));
const DATASET_NAME = "langgraph-prs";

logger.info(`Starting evals over ${DATASET.length} PRs...`);

/**
 * Format inputs for the open-swe system
 */
async function formatOpenSWEInputs(inputs: {
  repoOwner: string;
  repoName: string;
  baseCommit?: string;
  userInput: string;
}) {
  const targetRepository: TargetRepository = {
    owner: inputs.repoOwner,
    repo: inputs.repoName,
    branch: undefined,
    baseCommit: inputs.baseCommit,
  };

  const userMessageContent = `<request>
${inputs.userInput}
</request>`;

  return {
    messages: [{ type: "human", content: userMessageContent }],
    targetRepository,
    autoAcceptPlan: true,
  };
}

/**
 * Run open-swe instance and track manager, planner, programmer streams
 */
async function runOpenSWEWithStreamTracking(inputs: {
  repoOwner: string;
  repoName: string;
  baseCommit?: string;
  userInput: string;
}): Promise<OpenSWEStreamResults> {
  const result: OpenSWEStreamResults = {
    success: false,
  };

  try {
    const encryptionKey = process.env.SECRETS_ENCRYPTION_KEY;
    const githubPat = process.env.GITHUB_PAT;
    const githubInstallationId = process.env.GITHUB_INSTALLATION_ID;

    if (!encryptionKey || !githubPat || !githubInstallationId) {
      throw new Error(
        "SECRETS_ENCRYPTION_KEY, GITHUB_PAT, and GITHUB_INSTALLATION_ID environment variables are required"
      );
    }

    const encryptedGitHubToken = encryptSecret(githubPat, encryptionKey);

    const lgClient = createLangGraphClient({
      includeApiKey: true,
      defaultHeaders: { 
        [GITHUB_PAT]: encryptedGitHubToken,
        [GITHUB_INSTALLATION_ID]: githubInstallationId,
      },
    });

    const input = await formatOpenSWEInputs(inputs);
    const threadId = uuidv4();
    result.threadId = threadId;

    logger.info("Starting manager stream", {
      threadId,
      repo: `${inputs.repoOwner}/${inputs.repoName}`,
      baseCommit: inputs.baseCommit,
    });
    logger.info("input", input);
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
      return result; // Return early if manager run failed
    }
    logger.info("Manager run", {
      managerRun,
    });
    const managerState = managerRun as unknown as ManagerGraphState;
    const plannerSession = managerState?.plannerSession;

    if (!plannerSession) {
      logger.info("Agent did not create a planner session", {
        thread_id: threadId,
      });
      return result; // instead of skipping, we should award 0 points
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
      return result; // instead of skipping, we should award 0 points
    }

    const plannerState = plannerRun as unknown as PlannerGraphState;
    const programmerSession = plannerState?.programmerSession;

    if (!programmerSession) {
      logger.info("Agent did not create a programmer session", {
        thread_id: threadId,
      });
      return result; // instead of skipping, we should award 0 points
    }

    let programmerRun;
    try {
      programmerRun = await withRetry(() =>
        lgClient.runs.join(programmerSession.threadId, programmerSession.runId),
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
      return result; // instead of skipping, we should award 0 points
    }

    const programmerState = programmerRun as unknown as GraphState;
    const branchName = programmerState?.branchName;

    if (!branchName) {
      logger.info("Agent did not create a branch", {
        thread_id: threadId,
      });
      return result; // instead of skipping, we should award 0 points
    }

    logger.info("Agent completed. Created branch:", {
      branchName: branchName,
    });

    result.managerRunId = (managerRun as any).run_id;
    result.plannerRunId = plannerSession.runId;
    result.programmerRunId = programmerSession.runId;
    result.success = true;

  } catch (error) {
    result.error = error instanceof Error ? error.message : String(error);
    logger.error("Open-swe stream tracking failed", {
      threadId: result.threadId,
      error: result.error,
    });
  }

  return result;
}

/**
 * Process a single PR
 */
async function processPR(prData: PRData): Promise<PRProcessResult> {
  const result: PRProcessResult = {
    prNumber: prData.prNumber,
    repoName: prData.repoName,
    success: false,
    evalsFound: false,
    evalsFiles: [],
    testFiles: [],
  };
  const daytona = new Daytona({
    organizationId: process.env.DAYTONA_ORGANIZATION_ID,
  });
  let sandbox: Sandbox | undefined;

  try {
    logger.info(`Processing PR #${prData.prNumber}: ${prData.title}`);

    // Use test files from PR data (already fetched and stored)
    const testFiles = prData.testFiles || [];
    result.testFiles = testFiles;
    // Create sandbox
    sandbox = await daytona.create(DEFAULT_SANDBOX_CREATE_PARAMS);

    // Validate sandbox was created properly
    if (!sandbox || !sandbox.id) {
      throw new Error("Failed to create valid sandbox");
    }

    result.workspaceId = sandbox.id;
    logger.info(`Created sandbox: ${sandbox.id}`);

    // Use the hardcoded pre-merge commit SHA from the dataset
    const preMergeCommit = prData.preMergeCommitSha;
    logger.info(`Using pre-merge commit: ${preMergeCommit}`);
    result.preMergeSha = preMergeCommit;

    const targetRepository: TargetRepository = {
      owner: prData.repoOwner,
      repo: prData.repoName,
      branch: undefined,
      baseCommit: preMergeCommit,
    };
    const repoDir = getRepoAbsolutePath(targetRepository);

    // Clone and checkout the repository at the pre-merge commit
    const githubToken = process.env.GITHUB_PAT;
    if (!githubToken) {
      throw new Error("GITHUB_PAT environment variable is required");
    }

    await cloneRepo(sandbox, targetRepository, {
      githubInstallationToken: githubToken,
    });

    // Setup Python environment
    logger.info("Setting up Python environment...");
    const envSetupSuccess = await setupEnv(sandbox, repoDir);
    if (!envSetupSuccess) {
      logger.warn("Failed to setup Python environment, continuing anyway");
    }

    // Run open-swe instance with the pre-merge commit and track streams
    logger.info("Starting open-swe...");
    const openSWEResults = await runOpenSWEWithStreamTracking({
      repoOwner: prData.repoOwner,
      repoName: prData.repoName, 
      baseCommit: preMergeCommit,
      userInput: createPRFixPrompt(prData),
    });
    result.openSWEResults = openSWEResults;

    // Checkout test files from the merge commit to get the updated test files
    if (testFiles.length > 0) {
      logger.info(
        `Checking out test files from merge commit: ${prData.mergeCommitSha}`,
      );
      await checkoutFilesFromCommit({
        sandbox,
        repoDir,
        commitSha: prData.mergeCommitSha,
        filePaths: testFiles,
      });
    }

    // Run tests on detected test files
    if (testFiles.length > 0) {
      logger.info(
        `Running pytest on ${testFiles.length} detected test files...`,
      );
      const testResults = await runPytestOnFiles({
        sandbox,
        testFiles,
        repoDir,
        timeoutSec: 300,
      });
      result.testResults = testResults;

      logger.info(`Test execution completed for PR #${prData.prNumber}`, {
        totalTests: testResults.totalTests,
        passedTests: testResults.passedTests,
        failedTests: testResults.failedTests,
        success: testResults.success,
      });
    } else {
      logger.info(`No test files to run for PR #${prData.prNumber}`);
    }

    result.success = true;
    logger.info(`Successfully processed PR #${prData.prNumber}`);
  } catch (error) {
    result.error = error instanceof Error ? error.message : String(error);
    logger.error(`Failed to process PR #${prData.prNumber}:`, { error });
  } finally {
    // Cleanup sandbox
    if (sandbox) {
      try {
        await sandbox.delete();
        logger.info(`Deleted sandbox: ${sandbox.id}`);
      } catch (cleanupError) {
        logger.warn(`Failed to cleanup sandbox ${sandbox.id}:`, {
          cleanupError,
        });
      }
    }
  }

  return result;
}

ls.describe(DATASET_NAME, () => {
  ls.test.each(DATASET)(
    "Can process PR successfully",
    async ({ inputs: prData }) => {
      logger.info(`Processing PR #${prData.prNumber}: ${prData.title}`);

      const result = await processPR(prData);

      // Log results for visibility
      logger.info(`PR #${prData.prNumber} processing completed`, {
        success: result.success,
        evalsFound: result.evalsFound,
        evalsFilesCount: result.evalsFiles.length,
        testFilesCount: result.testFiles.length,
        testFiles: result.testFiles,
        testResults: result.testResults
          ? {
              totalTests: result.testResults.totalTests,
              passedTests: result.testResults.passedTests,
              failedTests: result.testResults.failedTests,
              success: result.testResults.success,
            }
          : null,
        openSWEResults: result.openSWEResults
          ? {
              threadId: result.openSWEResults.threadId,
              managerRunId: result.openSWEResults.managerRunId,
              plannerRunId: result.openSWEResults.plannerRunId,
              programmerRunId: result.openSWEResults.programmerRunId,
              branchName: result.openSWEResults.branchName,
              success: result.openSWEResults.success,
              error: result.openSWEResults.error,
            }
          : null,
        error: result.error,
        workspaceId: result.workspaceId,
        preMergeSha: result.preMergeSha,
      });

      // Assert that processing was successful
      if (!result.success) {
        throw new Error(`PR processing failed: ${result.error}`);
      }
    },
    300_000, // 5 minute timeout per PR
  );
});
