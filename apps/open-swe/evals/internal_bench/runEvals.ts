#!/usr/bin/env node
import dotenv from "dotenv";
import { Daytona, Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "../../src/utils/logger.js";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { DEFAULT_SANDBOX_CREATE_PARAMS } from "../../src/constants.js";
import { readFileSync, writeFileSync } from "fs";
import { cloneRepo, getPreMergeCommit } from "../../src/utils/github/git.js";
import { TargetRepository } from "@open-swe/shared/open-swe/types";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { setupEnv } from "../../src/utils/env-setup.js";

dotenv.config();
const logger = createLogger(LogLevel.INFO, "PR Processor");

interface PRData {
  url: string;
  html_url: string;
  diff_url: string;
  patch_url: string;
  repo_owner: string;
  repo_name: string;
  pr_number: number;
  merge_commit_sha: string;
  title: string;
  body: string;
  created_at: string;
  merged_at: string;
}

interface PRProcessResult {
  pr_number: number;
  repo_name: string;
  workspace_id?: string;
  success: boolean;
  evals_found: boolean;
  evals_files: string[];
  error?: string;
  pre_merge_sha?: string;
}

/**
 * Clone repository and checkout specific commit using the cloneRepo helper
 */
async function cloneAndCheckoutRepo(
  sandbox: Sandbox,
  prData: PRData,
  targetCommit: string
): Promise<void> {
  const targetRepository: TargetRepository = {
    owner: prData.repo_owner,
    repo: prData.repo_name,
    branch: "main", 
    baseCommit: targetCommit
  };

  logger.info(`Cloning repository: ${prData.repo_owner}/${prData.repo_name} at commit ${targetCommit}`);

  await cloneRepo(sandbox, targetRepository, {
    githubInstallationToken: process.env.GITHUB_TOKEN || "dummy_token"
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
  console.log(process.env.DAYTONA_ORGANIZATION_ID);
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
      baseCommit: undefined
    };
    const repoDir = getRepoAbsolutePath(targetRepository);
    
    // First, clone and checkout the merge commit to get the parent
    await cloneAndCheckoutRepo(sandbox, prData, prData.merge_commit_sha);
    
    // Get the pre-merge commit (parent of merge commit)
    const preMergeSha = await getPreMergeCommit(sandbox, repoDir, prData.merge_commit_sha);
    result.pre_merge_sha = preMergeSha;
    
    logger.info(`Pre-merge commit: ${preMergeSha}`);
    
    // Checkout the pre-merge commit to see the state before the PR was merged
    const checkoutPreMergeResult = await sandbox.process.executeCommand(
      `git checkout ${preMergeSha}`,
      repoDir,
      undefined,
      TIMEOUT_SEC
    );
    
    if (checkoutPreMergeResult.exitCode !== 0) {
      throw new Error(`Failed to checkout pre-merge commit: ${checkoutPreMergeResult.result}`);
    }
    
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
        logger.warn(`Failed to cleanup sandbox ${sandbox.id}:`, { cleanupError });
      }
    }
  }
  
  return result;
}

/**
 * Process all PRs or a single PR
 */
async function main() {
  const args = process.argv.slice(2);
  
  if (args.length === 0) {
    console.log("Usage:");
    console.log("  npm run process-prs [--single PR_NUMBER]");
    process.exit(1);
  }
  // Load PRs data
  const prsData: PRData[] = JSON.parse(readFileSync("static/langgraph_prs.json", "utf8"));
  logger.info(`Loaded ${prsData.length} PRs`);
  
  let prsToProcess: PRData[];
  
  if (args.includes("--single")) {
    const prNumberIndex = args.indexOf("--single") + 1;
    if (prNumberIndex >= args.length) {
      logger.error("PR number not specified after --single flag");
      process.exit(1);
    }
    
    const targetPrNumber = parseInt(args[prNumberIndex]);
    const targetPr = prsData.find(pr => pr.pr_number === targetPrNumber);
    
    if (!targetPr) {
      logger.error(`PR #${targetPrNumber} not found in dataset`);
      process.exit(1);
    }
    
    prsToProcess = [targetPr];
    logger.info(`Processing single PR: #${targetPrNumber}`);
  } else {
    prsToProcess = prsData;
    logger.info(`Processing all ${prsData.length} PRs`);
  }
  
  const results: PRProcessResult[] = [];
  
  for (let i = 0; i < prsToProcess.length; i++) {
    const pr = prsToProcess[i];
    logger.info(`\n=== Processing PR ${i + 1}/${prsToProcess.length}: #${pr.pr_number} ===`);
    
    const result = await processPR(pr);
    results.push(result);
    
    // Save results after each PR in case of interruption
    const outputFile = args.includes("--single") 
      ? `pr_${pr.pr_number}_result.json` 
      : "pr_processing_results.json";
    
    writeFileSync(outputFile, JSON.stringify(results, null, 2));
    logger.info(`Results saved to: ${outputFile}`);
    
    // Small delay between processing
    if (i < prsToProcess.length - 1) {
      logger.info("Waiting 2 seconds before next PR...");
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
  }
  
  // Summary
  const successful = results.filter(r => r.success).length;
  const withEvals = results.filter(r => r.evals_found).length;
  
  logger.info(`\n=== Processing Complete ===`);
  logger.info(`Successfully processed: ${successful}/${results.length} PRs`);
  logger.info(`PRs with evals/ directory: ${withEvals}/${results.length} PRs`);
  
  if (withEvals > 0) {
    logger.info("\nPRs with evals directories:");
    results.filter(r => r.evals_found).forEach(r => {
      logger.info(`  PR #${r.pr_number} (${r.repo_name}): ${r.evals_files.length} files`);
    });
  }
}

// Run the main function
main().catch(error => {
  logger.error("Script failed:", { error });
  process.exit(1);
});