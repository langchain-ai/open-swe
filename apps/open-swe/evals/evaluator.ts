import "dotenv/config";
import { OpenSWEInput, CodeTestDetails } from "./open-swe-types.js";
import { Daytona, Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "../src/utils/logger.js";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { DEFAULT_SANDBOX_CREATE_PARAMS } from "../src/constants.js";
import { TargetRepository } from "@open-swe/shared/open-swe/types";
import { cloneRepo } from "../src/utils/github/git.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { SimpleEvaluationResult } from "langsmith/vitest";
import { runRuffLint, runMyPyTypeCheck } from "./tests.js";
import { setupEnv, ENV_CONSTANTS } from "../src/utils/env-setup.js";

const logger = createLogger(LogLevel.INFO, "Evaluator ");

// Use shared constants from env-setup utility
const { RUN_PYTHON_IN_VENV } = ENV_CONSTANTS;

/**
 * Runs ruff and mypy analysis on all Python files in the repository
 */
async function runCodeTests(
  sandbox: Sandbox,
  absoluteRepoDir: string,
): Promise<{ ruffScore: number; mypyScore: number; details: CodeTestDetails }> {
  logger.info("Running code analysis on all Python files in repository");

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
    }),
    runMyPyTypeCheck(sandbox, {
      command: `${RUN_PYTHON_IN_VENV} -m mypy . --no-error-summary --show-error-codes --no-color-output`,
      workingDir: absoluteRepoDir,
      env: undefined,
      timeoutSec: TIMEOUT_SEC * 3,
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

  const githubToken = process.env.GITHUB_PAT;
  if (!githubToken) {
    throw new Error("GITHUB_PAT environment variable is not set");
  }

  const daytonaInstance = new Daytona();
  const solutionBranch = output.branchName;
  logger.info("Creating sandbox...", {
    repo: openSWEInputs.repo,
    originalBranch: openSWEInputs.branch,
    solutionBranch,
    user_input: openSWEInputs.user_input.substring(0, 100) + "...",
  });

  const sandbox = await daytonaInstance.create(DEFAULT_SANDBOX_CREATE_PARAMS);

  try {
    await cloneRepo(sandbox, output.targetRepository, {
      githubInstallationToken: githubToken,
      stateBranchName: solutionBranch,
    });

    const absoluteRepoDir = getRepoAbsolutePath(output.targetRepository);

    const envSetupSuccess = await setupEnv(sandbox, absoluteRepoDir);
    if (!envSetupSuccess) {
      logger.error("Failed to setup environment");
      return [
        {
          key: "overall-score",
          score: 0,
        },
      ];
    }

    const analysisResult = await runCodeTests(sandbox, absoluteRepoDir);

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
      await sandbox.delete();
      logger.info("Sandbox cleaned up successfully");
    } catch (cleanupError) {
      logger.error("Failed to cleanup sandbox", { cleanupError });
    }
  }
}
