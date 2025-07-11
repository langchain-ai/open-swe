import "dotenv/config";
import { OpenSWEInput, RuffIssue, codeTestDetails } from "./open-swe-types.js";
import { Daytona, Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "../src/utils/logger.js";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { DEFAULT_SANDBOX_CREATE_PARAMS } from "../src/constants.js";
import { TargetRepository } from "@open-swe/shared/open-swe/types";
import { cloneRepo } from "../src/utils/github/git.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { SimpleEvaluationResult } from "langsmith/vitest";
import { ruffPromise, mypyPromise } from "./tests.js";

const logger = createLogger(LogLevel.INFO, "Evaluator ");

const VENV_PATH = ".venv";
const RUN_PYTHON_IN_VENV = `${VENV_PATH}/bin/python`;
const RUN_PIP_IN_VENV = `${VENV_PATH}/bin/pip`;

/**
 * Setup Python environment with requirements.txt + ruff + mypy
 */
async function setupEnv(
  sandbox: Sandbox,
  absoluteRepoDir: string,
): Promise<boolean> {
  logger.info("Setting up Python environment...");

  const createVenvCommand = "python -m venv .venv";
  const createVenvRes = await sandbox.process.executeCommand(
    createVenvCommand,
    absoluteRepoDir,
    undefined,
    TIMEOUT_SEC,
  );
  if (createVenvRes.exitCode !== 0) {
    logger.error("Failed to create virtual environment", {
      createVenvCommand,
      createVenvRes,
    });
    return false;
  }

  const upgradePipRes = await sandbox.process.executeCommand(
    `${RUN_PIP_IN_VENV} install --upgrade pip`,
    absoluteRepoDir,
    undefined,
    TIMEOUT_SEC,
  );
  if (upgradePipRes.exitCode !== 0) {
    logger.warn("Failed to upgrade pip, continuing anyway", { upgradePipRes });
  }

  const requirementsExistRes = await sandbox.process.executeCommand(
    "test -f requirements.txt",
    absoluteRepoDir,
    undefined,
    TIMEOUT_SEC,
  );

  if (requirementsExistRes.exitCode === 0) {
    logger.info("Found requirements.txt, installing...");
    const installReqRes = await sandbox.process.executeCommand(
      `${RUN_PIP_IN_VENV} install -r requirements.txt`,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC * 3,
    );
    if (installReqRes.exitCode !== 0) {
      logger.warn("Failed to install requirements.txt, continuing anyway", {
        installReqRes,
      });
    }
  } else {
    logger.info("No requirements.txt found, skipping repository dependencies");
  }

  const installAnalysisToolsRes = await sandbox.process.executeCommand(
    `${RUN_PIP_IN_VENV} install ruff mypy`,
    absoluteRepoDir,
    undefined,
    TIMEOUT_SEC,
  );
  if (installAnalysisToolsRes.exitCode !== 0) {
    logger.error("Failed to install ruff and mypy", {
      installAnalysisToolsRes,
    });
    return false;
  }

  logger.info("Environment setup completed successfully");
  return true;
}

/**
 * Run ruff and mypy analysis on all Python files in the repository
 */
async function runCodeTests(
  sandbox: Sandbox,
  absoluteRepoDir: string,
): Promise<{ ruffScore: number; mypyScore: number; details: codeTestDetails }> {
  logger.info("Running code analysis on all Python files in repository");

  const testResults = {
    ruffScore: 0,
    mypyScore: 0,
    details: {
      ruff: {
        issues: [] as RuffIssue[],
        error: null as Error | null,
      },
      mypy: {
        issues: [] as string[],
        error: null as Error | null,
      },
    },
  };

  const [ruffResult, mypyResult] = await Promise.allSettled([
    ruffPromise(sandbox, {
      command: `${RUN_PYTHON_IN_VENV} -m ruff check . --output-format=json`,
      workingDir: absoluteRepoDir,
      env: undefined,
      timeoutSec: TIMEOUT_SEC * 3,
    }),
    mypyPromise(sandbox, {
      command: `${RUN_PYTHON_IN_VENV} -m mypy . --no-error-summary --show-error-codes --no-color-output`,
      workingDir: absoluteRepoDir,
      env: undefined,
      timeoutSec: TIMEOUT_SEC * 3,
    }),
  ]);

  if (ruffResult.status === "fulfilled") {
    testResults.details.ruff.issues = ruffResult.value.issues;
    testResults.details.ruff.error = ruffResult.value.error;
    testResults.ruffScore = ruffResult.value.ruffScore;
  } else {
    logger.error("Ruff test promise was rejected", {
      reason: ruffResult.reason,
    });
    testResults.details.ruff.error = ruffResult.reason;
    testResults.ruffScore = 0;
  }

  if (mypyResult.status === "fulfilled") {
    testResults.details.mypy.issues = mypyResult.value.issues;
    testResults.details.mypy.error = mypyResult.value.error;
    testResults.mypyScore = mypyResult.value.mypyScore;
  } else {
    logger.error("MyPy test promise was rejected", {
      reason: mypyResult.reason,
    });
    testResults.details.mypy.error = mypyResult.reason;
    testResults.mypyScore = 0;
  }

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
  logger.info("Creating sandbox...", {
    repo: openSWEInputs.repo,
    originalBranch: openSWEInputs.branch,
    solutionBranch: output.branchName,
    user_input: openSWEInputs.user_input.substring(0, 100) + "...",
  });

  const sandbox = await daytonaInstance.create(DEFAULT_SANDBOX_CREATE_PARAMS);

  try {
    const res = await cloneRepo(sandbox, output.targetRepository, {
      githubInstallationToken: githubToken,
    });
    if (res.exitCode !== 0) {
      logger.error("Failed to clone repository", {
        targetRepository: output.targetRepository,
        cloneResult: res,
      });
      throw new Error("Failed to clone repository");
    }

    const absoluteRepoDir = getRepoAbsolutePath(output.targetRepository);

    const solutionBranch = output.branchName;
    logger.info(`Checking out agent's solution branch: ${solutionBranch}`);

    const checkoutBranchRes = await sandbox.process.executeCommand(
      `git checkout ${solutionBranch}`,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );
    if (checkoutBranchRes.exitCode !== 0) {
      logger.error("Failed to checkout solution branch", {
        solutionBranch,
        checkoutResult: checkoutBranchRes,
      });
      throw new Error(`Failed to checkout solution branch: ${solutionBranch}`);
    }

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
      solutionBranch: output.branchName,
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
