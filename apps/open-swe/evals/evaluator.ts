import "dotenv/config";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { OpenSWEInput, CodeTestDetails, FeatureScopeOptions } from "./open-swe-types.js";
import type { Sandbox } from "../src/utils/sandbox.js";
import {
  createDockerSandbox,
  deleteSandbox,
  getSandboxMetadata,
  stopSandbox,
} from "../src/utils/sandbox.js";
import { createLogger, LogLevel } from "../src/utils/logger.js";
import { TIMEOUT_SEC } from "@openswe/shared/constants";
import { uploadRepoToContainer } from "@openswe/shared/upload-repo-to-container";
import { TargetRepository } from "@openswe/shared/open-swe/types";
import { getRepoAbsolutePath } from "@openswe/shared/git";
import { SimpleEvaluationResult } from "langsmith/vitest";
import { runRuffLint, runMyPyTypeCheck } from "./tests.js";
import { setupEnv, ENV_CONSTANTS } from "../src/utils/env-setup.js";
import { SANDBOX_DOCKER_IMAGE } from "../src/constants.js";

const logger = createLogger(LogLevel.INFO, "Evaluator ");
const execFileAsync = promisify(execFile);

const FEATURE_SCOPE_ENV_FLAG = "OPEN_SWE_FEATURE_SCOPED_EVAL";
const FEATURE_SCOPE_DEBUG_FLAG = "OPEN_SWE_FEATURE_SCOPE_DEBUG";

const parseBooleanFlag = (value: string | undefined, fallback: boolean): boolean => {
  if (value === undefined) return fallback;
  const normalized = value.trim().toLowerCase();
  if (["1", "true", "yes", "y", "on"].includes(normalized)) return true;
  if (["0", "false", "no", "n", "off"].includes(normalized)) return false;
  return fallback;
};

const isFeatureScopeEnabled = (): boolean =>
  parseBooleanFlag(process.env[FEATURE_SCOPE_ENV_FLAG], false);

const isFeatureScopeDebug = (): boolean =>
  parseBooleanFlag(process.env[FEATURE_SCOPE_DEBUG_FLAG], false);

const normalizeChangedPath = (entry: string): string =>
  entry.trim().replace(/\\/g, "/").replace(/^\.\//, "");

const collectHostChangedPaths = async (
  repoDir: string,
  baseBranch: string | undefined,
  headBranch: string | undefined,
): Promise<string[] | undefined> => {
  if (!baseBranch || !headBranch) return undefined;

  try {
    const { stdout } = await execFileAsync("git", [
      "diff",
      `${baseBranch}...${headBranch}`,
      "--name-only",
    ], {
      cwd: repoDir,
    });

    const lines = stdout
      .split("\n")
      .map(normalizeChangedPath)
      .filter(Boolean);
    return lines.length > 0 ? lines : undefined;
  } catch (error) {
    logger.warn("Unable to determine changed paths for feature-scoped evaluation", {
      error,
    });
    return undefined;
  }
};

type FeatureScopeConfig = {
  enabled: boolean;
  baseBranch?: string;
  headBranch?: string;
  changedPaths?: string[];
  artifactPaths?: string[];
};

// Use shared constants from env-setup utility
const { RUN_PYTHON_IN_VENV } = ENV_CONSTANTS;

/**
 * Runs ruff and mypy analysis on all Python files in the repository
 */
async function runCodeTests(
  sandbox: Sandbox,
  absoluteRepoDir: string,
  featureScopeConfig?: FeatureScopeConfig,
): Promise<{ ruffScore: number; mypyScore: number; details: CodeTestDetails }> {
  logger.info("Running code analysis on all Python files in repository");

  const featureScope: FeatureScopeOptions | undefined = featureScopeConfig?.enabled
    ? {
        enabled: true,
        graphPath: path.join(absoluteRepoDir, "features", "graph", "graph.yaml"),
        baseBranch: featureScopeConfig.baseBranch,
        headBranch: featureScopeConfig.headBranch,
        changedPaths: featureScopeConfig.changedPaths,
        artifactPaths: featureScopeConfig.artifactPaths,
      }
    : undefined;

  if (featureScope?.enabled) {
    logger.info("Feature-scoped evaluation enabled", {
      changedPaths: featureScope.changedPaths?.length ?? 0,
      baseBranch: featureScope.baseBranch,
      headBranch: featureScope.headBranch,
    });
  }

  const testResults: {
    ruffScore: number;
    mypyScore: number;
    details: CodeTestDetails;
  } = {
    ruffScore: 0,
    mypyScore: 0,
    details: {
      ruff: {
        issues: [],
        error: null,
      },
      mypy: {
        issues: [],
        error: null,
      },
    },
  };

  const [ruffLint, mypyCheck] = await Promise.all([
    runRuffLint(sandbox, {
      command: `${RUN_PYTHON_IN_VENV} -m ruff check . --output-format=json`,
      workingDir: absoluteRepoDir,
      env: undefined,
      timeoutSec: TIMEOUT_SEC * 3,
      featureScope,
    }),
    runMyPyTypeCheck(sandbox, {
      command: `${RUN_PYTHON_IN_VENV} -m mypy . --no-error-summary --show-error-codes --no-color-output`,
      workingDir: absoluteRepoDir,
      env: undefined,
      timeoutSec: TIMEOUT_SEC * 3,
      featureScope,
    }),
  ]);

  Object.assign(testResults, {
    ruffScore: ruffLint.ruffScore,
    mypyScore: mypyCheck.mypyScore,
    details: {
      ruff: {
        issues: ruffLint.issues,
        error: ruffLint.error,
      },
      mypy: {
        issues: mypyCheck.issues,
        error: mypyCheck.error,
      },
    },
  });

  logger.info("Code tests completed", {
    ruffScore: testResults.ruffScore,
    mypyScore: testResults.mypyScore,
    ruffIssues: testResults.details.ruff.issues.length,
    mypyIssues: testResults.details.mypy.issues.length,
  });

  return testResults;
}

/**
 * Main evaluator function for OpenSWE code analysis
 */
export async function evaluator(inputs: {
  openSWEInputs: OpenSWEInput;
  output: {
    branchName: string;
    targetRepository: TargetRepository;
  };
}): Promise<SimpleEvaluationResult[]> {
  const { openSWEInputs, output } = inputs;

  const solutionBranch = output.branchName;
  logger.info("Creating sandbox...", {
    repo: openSWEInputs.repo,
    originalBranch: openSWEInputs.branch,
    solutionBranch,
    user_input: openSWEInputs.user_input.substring(0, 100) + "...",
  });

  const localRepoDir = getRepoAbsolutePath(output.targetRepository);
  const featureScopeEnabled = isFeatureScopeEnabled();
  const hostChangedPaths = featureScopeEnabled
    ? await collectHostChangedPaths(
        localRepoDir,
        openSWEInputs.branch,
        solutionBranch,
      )
    : undefined;

  if (featureScopeEnabled && isFeatureScopeDebug()) {
    logger.info("Feature-scope host diff", {
      changedPaths: hostChangedPaths?.length ?? 0,
    });
  }

  const sandbox = await createDockerSandbox(SANDBOX_DOCKER_IMAGE, {
    hostRepoPath: localRepoDir,
    repoName: output.targetRepository.repo,
    commitOnChange: false,
  });

  try {
    await uploadRepoToContainer({
      containerId: sandbox.id,
      localRepoPath: localRepoDir,
    });
    logger.info("Repository uploaded to sandbox", {
      repo: output.targetRepository.repo,
    });
    const metadata = getSandboxMetadata(sandbox.id);
    const containerRepoDir =
      metadata?.containerRepoPath ?? `/workspace/${output.targetRepository.repo}`;

    const envSetupSuccess = await setupEnv(sandbox, containerRepoDir);
    if (!envSetupSuccess) {
      logger.error("Failed to setup environment");
      return [
        {
          key: "overall-score",
          score: 0,
        },
      ];
    }

    const analysisResult = await runCodeTests(sandbox, containerRepoDir, {
      enabled: featureScopeEnabled,
      baseBranch: openSWEInputs.branch,
      headBranch: solutionBranch,
      changedPaths: hostChangedPaths,
    });

    const overallScore = analysisResult.ruffScore + analysisResult.mypyScore;

    logger.info("Evaluation completed", {
      overallScore,
      ruffScore: analysisResult.ruffScore,
      mypyScore: analysisResult.mypyScore,
      repo: openSWEInputs.repo,
      originalBranch: openSWEInputs.branch,
      solutionBranch,
    });

    return [
      {
        key: "overall-score",
        score: overallScore,
      },
      {
        key: "ruff-score",
        score: analysisResult.ruffScore,
      },
      {
        key: "mypy-score",
        score: analysisResult.mypyScore,
      },
    ];
  } catch (error) {
    logger.error("Evaluation failed with error", { error });
    return [
      {
        key: "overall-score",
        score: 0,
      },
    ];
  } finally {
    try {
      await stopSandbox(sandbox.id);
      await deleteSandbox(sandbox.id);
      logger.info("Sandbox cleaned up successfully");
    } catch (cleanupError) {
      logger.error("Failed to cleanup sandbox", { cleanupError });
    }
  }
}
