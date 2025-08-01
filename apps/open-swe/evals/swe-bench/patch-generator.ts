import { Daytona, Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "../../src/utils/logger.js";
import { TargetRepository } from "@open-swe/shared/open-swe/types";
import { cloneRepo } from "../../src/utils/github/git.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { DEFAULT_SANDBOX_CREATE_PARAMS } from "../../src/constants.js";

const logger = createLogger(LogLevel.INFO, "PatchGenerator");

/**
 * Options for generating a patch from a branch
 */
export interface PatchGeneratorOptions {
  /**
   * The branch containing the agent's changes
   */
  branchName: string;

  /**
   * The base branch to diff against (e.g., "main", "master")
   * If not provided, will attempt to detect the default branch
   */
  baseBranch?: string;

  /**
   * Target repository information
   */
  targetRepository: TargetRepository;

  /**
   * GitHub token for authentication
   */
  githubToken?: string;

  /**
   * Whether to exclude test files from the patch
   * Default: true (SWE-bench typically wants non-test changes only)
   */
  excludeTests?: boolean;

  /**
   * Custom sandbox to use (if not provided, creates a new one)
   */
  sandbox?: Sandbox;

  /**
   * Whether to keep the sandbox after generating the patch
   * Default: false
   */
  keepSandbox?: boolean;
}

/**
 * Result from patch generation
 */
export interface PatchGeneratorResult {
  /**
   * The generated patch as a unified diff string
   * Will be null if no changes were found
   */
  patch: string | null;

  /**
   * List of files modified in the patch
   */
  modifiedFiles: string[];

  /**
   * Any errors encountered during patch generation
   */
  error?: string;

  /**
   * The sandbox used (if keepSandbox was true)
   */
  sandbox?: Sandbox;
}

/**
 * Generates a git diff patch from an agent's branch
 * This patch can be used as the model_patch in SWE-bench predictions
 */
export async function generatePatchFromBranch(
  options: PatchGeneratorOptions,
): Promise<PatchGeneratorResult> {
  const {
    branchName,
    baseBranch,
    targetRepository,
    githubToken,
    excludeTests = true,
    sandbox: providedSandbox,
    keepSandbox = false,
  } = options;

  let sandbox: Sandbox | undefined = providedSandbox;
  let shouldCleanup = false;

  try {
    // Create sandbox if not provided
    if (!sandbox) {
      logger.info("Creating new sandbox for patch generation...");
      const daytonaInstance = new Daytona();
      sandbox = await daytonaInstance.create(DEFAULT_SANDBOX_CREATE_PARAMS);
      shouldCleanup = !keepSandbox;
    }

    // Clone the repository
    logger.info("Cloning repository...", {
      repo: `${targetRepository.owner}/${targetRepository.repo}`,
      branch: branchName,
    });

    await cloneRepo(sandbox, targetRepository, {
      githubInstallationToken: githubToken || "",
      stateBranchName: branchName,
    });

    const absoluteRepoDir = getRepoAbsolutePath(targetRepository);

    // Determine the base branch if not provided
    let actualBaseBranch = baseBranch;
    if (!actualBaseBranch) {
      logger.info("Detecting default branch...");
      const defaultBranchResult = await sandbox.process.executeCommand(
        "git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@'",
        absoluteRepoDir,
        undefined,
        TIMEOUT_SEC,
      );

      if (
        defaultBranchResult.exitCode === 0 &&
        defaultBranchResult.result.trim()
      ) {
        actualBaseBranch = defaultBranchResult.result.trim();
        logger.info(`Detected default branch: ${actualBaseBranch}`);
      } else {
        // Fallback to common default branches
        const commonBranches = ["main", "master"];
        for (const branch of commonBranches) {
          const checkResult = await sandbox.process.executeCommand(
            `git rev-parse --verify origin/${branch}`,
            absoluteRepoDir,
            undefined,
            TIMEOUT_SEC,
          );
          if (checkResult.exitCode === 0) {
            actualBaseBranch = branch;
            logger.info(`Using fallback branch: ${actualBaseBranch}`);
            break;
          }
        }
      }

      if (!actualBaseBranch) {
        throw new Error("Could not determine base branch");
      }
    }

    // Fetch the base branch to ensure we have it
    logger.info(`Fetching base branch: ${actualBaseBranch}`);
    const fetchResult = await sandbox.process.executeCommand(
      `git fetch origin ${actualBaseBranch}:${actualBaseBranch}`,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );

    if (fetchResult.exitCode !== 0) {
      logger.warn(`Failed to fetch base branch, trying to continue anyway`, {
        error: fetchResult.result,
      });
    }

    // Generate the diff
    logger.info("Generating patch...", {
      from: `origin/${actualBaseBranch}`,
      to: branchName,
    });

    // Build the git diff command
    let diffCommand = `git diff origin/${actualBaseBranch}...${branchName}`;

    // Add exclusions for test files if requested
    if (excludeTests) {
      // Common test file patterns to exclude
      const testExclusions = [
        ":(exclude)**/test_*.py",
        ":(exclude)**/*_test.py",
        ":(exclude)**/tests/**",
        ":(exclude)**/test/**",
        ":(exclude)**/*.test.ts",
        ":(exclude)**/*.test.js",
        ":(exclude)**/*.spec.ts",
        ":(exclude)**/*.spec.js",
        ":(exclude)**/__tests__/**",
        ":(exclude)**/conftest.py",
      ];
      diffCommand += ` -- . ${testExclusions.join(" ")}`;
    }

    const diffResult = await sandbox.process.executeCommand(
      diffCommand,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC * 2, // Give more time for large diffs
    );

    if (diffResult.exitCode !== 0) {
      throw new Error(`Failed to generate diff: ${diffResult.result}`);
    }

    const patch = diffResult.result.trim();

    // Get list of modified files
    logger.info("Getting list of modified files...");
    const filesCommand = excludeTests
      ? `git diff --name-only origin/${actualBaseBranch}...${branchName} -- . ${[
          ":(exclude)**/test_*.py",
          ":(exclude)**/*_test.py",
          ":(exclude)**/tests/**",
          ":(exclude)**/test/**",
          ":(exclude)**/*.test.ts",
          ":(exclude)**/*.test.js",
          ":(exclude)**/*.spec.ts",
          ":(exclude)**/*.spec.js",
          ":(exclude)**/__tests__/**",
          ":(exclude)**/conftest.py",
        ].join(" ")}`
      : `git diff --name-only origin/${actualBaseBranch}...${branchName}`;

    const filesResult = await sandbox.process.executeCommand(
      filesCommand,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );

    const modifiedFiles =
      filesResult.exitCode === 0
        ? filesResult.result
            .split("\n")
            .map((f) => f.trim())
            .filter((f) => f.length > 0)
        : [];

    logger.info(`Generated patch with ${modifiedFiles.length} modified files`, {
      files: modifiedFiles.slice(0, 5), // Log first 5 files
      totalFiles: modifiedFiles.length,
      patchSize: patch.length,
    });

    return {
      patch: patch.length > 0 ? patch : null,
      modifiedFiles,
      sandbox: keepSandbox ? sandbox : undefined,
    };
  } catch (error) {
    logger.error("Failed to generate patch", { error });
    return {
      patch: null,
      modifiedFiles: [],
      error: error instanceof Error ? error.message : String(error),
      sandbox: keepSandbox ? sandbox : undefined,
    };
  } finally {
    // Cleanup sandbox if needed
    if (sandbox && shouldCleanup && !providedSandbox) {
      try {
        logger.info("Cleaning up sandbox...");
        await sandbox.delete();
      } catch (cleanupError) {
        logger.warn("Failed to cleanup sandbox", { error: cleanupError });
      }
    }
  }
}

/**
 * Validates that a patch is properly formatted and can be applied
 */
export function validatePatch(patch: string): boolean {
  if (!patch || patch.trim().length === 0) {
    return false;
  }

  // Check for basic git diff format
  const lines = patch.split("\n");
  const hasDiffHeader = lines.some((line) => line.startsWith("diff --git"));
  const hasFileHeaders = lines.some(
    (line) => line.startsWith("---") || line.startsWith("+++"),
  );
  const hasHunkHeaders = lines.some((line) =>
    line.match(/^@@\s+-\d+,?\d*\s+\+\d+,?\d*\s+@@/),
  );

  return hasDiffHeader && hasFileHeaders && hasHunkHeaders;
}

/**
 * Extracts file paths from a patch
 */
export function extractFilesFromPatch(patch: string): string[] {
  const files: string[] = [];
  const lines = patch.split("\n");

  for (const line of lines) {
    // Match diff --git a/path/to/file b/path/to/file
    const diffMatch = line.match(/^diff --git a\/(.+) b\/(.+)$/);
    if (diffMatch) {
      // Use the 'b' path (destination)
      files.push(diffMatch[2]);
    }
  }

  return [...new Set(files)]; // Remove duplicates
}

/**
 * Filters a patch to only include changes to specific files
 */
export function filterPatchByFiles(
  patch: string,
  includeFiles: string[],
): string {
  const lines = patch.split("\n");
  const filteredLines: string[] = [];
  let currentFile: string | null = null;
  let includeCurrentFile = false;

  for (const line of lines) {
    // Check if this is a new file diff
    const diffMatch = line.match(/^diff --git a\/(.+) b\/(.+)$/);
    if (diffMatch) {
      currentFile = diffMatch[2];
      includeCurrentFile = includeFiles.includes(currentFile);
    }

    // Include the line if we're including the current file
    if (includeCurrentFile) {
      filteredLines.push(line);
    }
  }

  return filteredLines.join("\n");
}

/**
 * Counts the number of additions and deletions in a patch
 */
export function countPatchChanges(patch: string): {
  additions: number;
  deletions: number;
} {
  const lines = patch.split("\n");
  let additions = 0;
  let deletions = 0;

  for (const line of lines) {
    if (line.startsWith("+") && !line.startsWith("+++")) {
      additions++;
    } else if (line.startsWith("-") && !line.startsWith("---")) {
      deletions++;
    }
  }

  return { additions, deletions };
}
