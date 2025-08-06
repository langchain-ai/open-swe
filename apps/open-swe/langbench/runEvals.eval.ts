import * as ls from "langsmith/vitest";
import dotenv from "dotenv";
import { Daytona, Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "../src/utils/logger.js";
import { DEFAULT_SANDBOX_CREATE_PARAMS } from "../src/constants.js";
import { readFileSync } from "fs";
import { cloneRepo } from "../src/utils/github/git.js";
import { TargetRepository } from "@open-swe/shared/open-swe/types";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { setupEnv } from "../src/utils/env-setup.js";
import { PRData, PRProcessResult } from "./types.js";

dotenv.config();
const logger = createLogger(LogLevel.INFO, "PR Processor");

// Load PRs data
const prsData: PRData[] = JSON.parse(
  readFileSync("static/langgraph_prs.json", "utf8"),
);

const DATASET = prsData.map((pr) => ({ inputs: pr }));
const DATASET_NAME = "langgraph-prs";

logger.info(`Starting evals over ${DATASET.length} PRs...`);

/**
 * Clone repository and checkout specific commit using the cloneRepo helper
 */
async function cloneAndCheckoutRepo(
  sandbox: Sandbox,
  prData: PRData,
  targetCommit: string,
): Promise<void> {
  const targetRepository: TargetRepository = {
    owner: prData.repo_owner,
    repo: prData.repo_name,
    branch: "main",
    baseCommit: targetCommit,
  };

  logger.info(
    `Cloning repository: ${prData.repo_owner}/${prData.repo_name} at commit ${targetCommit}`,
  );

  await cloneRepo(sandbox, targetRepository, {
    githubInstallationToken: process.env.GITHUB_TOKEN || "dummy_token",
  });

  logger.info(`Successfully cloned and checked out commit: ${targetCommit}`);
}

/**
 * Process a single PR
 */
async function processPR(prData: PRData): Promise<PRProcessResult> {
  const result: PRProcessResult = {
    pr_number: prData.pr_number,
    repo_name: prData.repo_name,
    success: false,
    evals_found: false,
    evals_files: [],
  };
  const daytona = new Daytona({
    organizationId: process.env.DAYTONA_ORGANIZATION_ID,
  });
  let sandbox: Sandbox | undefined;

  try {
    logger.info(`Processing PR #${prData.pr_number}: ${prData.title}`);

    // Create sandbox
    sandbox = await daytona.create(DEFAULT_SANDBOX_CREATE_PARAMS);
    result.workspace_id = sandbox.id;

    logger.info(`Created sandbox: ${sandbox.id}`);

    const targetRepository: TargetRepository = {
      owner: prData.repo_owner,
      repo: prData.repo_name,
      branch: "main",
      baseCommit: prData.merge_commit_sha,
    };
    const repoDir = getRepoAbsolutePath(targetRepository);

    // Clone and checkout the repository at the merge commit
    await cloneAndCheckoutRepo(sandbox, prData, prData.merge_commit_sha);

    // Setup Python environment
    logger.info("Setting up Python environment...");
    const envSetupSuccess = await setupEnv(sandbox, repoDir);
    if (!envSetupSuccess) {
      logger.warn("Failed to setup Python environment, continuing anyway");
    }

    result.success = true;
    logger.info(`Successfully processed PR #${prData.pr_number}`);
  } catch (error) {
    result.error = error instanceof Error ? error.message : String(error);
    logger.error(`Failed to process PR #${prData.pr_number}:`, { error });
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
      logger.info(`Processing PR #${prData.pr_number}: ${prData.title}`);

      const result = await processPR(prData);

      // Log results for visibility
      logger.info(`PR #${prData.pr_number} processing completed`, {
        success: result.success,
        evals_found: result.evals_found,
        evals_files_count: result.evals_files.length,
        error: result.error,
        workspace_id: result.workspace_id,
        pre_merge_sha: result.pre_merge_sha,
      });

      // Assert that processing was successful
      if (!result.success) {
        throw new Error(`PR processing failed: ${result.error}`);
      }
    },
    300_000, // 5 minute timeout per PR
  );
});
