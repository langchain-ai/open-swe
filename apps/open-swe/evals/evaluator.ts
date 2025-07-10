import "dotenv/config";
import { OpenSWEInput } from "./open-swe-types.js";
import { Daytona, Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "../src/utils/logger.js";
import { SNAPSHOT_NAME, TIMEOUT_SEC } from "@open-swe/shared/constants";
import { TargetRepository } from "@open-swe/shared/open-swe/types";
import { cloneRepo } from "../src/utils/github/git.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { SimpleEvaluationResult } from "langsmith/vitest";

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
): Promise<{ ruffScore: number; mypyScore: number; details: any }> {
  logger.info("Running code analysis on all Python files in repository");

  const testResults = {
    ruffScore: 0,
    mypyScore: 0,
    details: {
      ruff: {
        issues: [] as any[],
        exitCode: -1,
        output: "",
        error: null as any,
      },
      mypy: {
        issues: [] as string[],
        exitCode: -1,
        output: "",
        error: null as any,
      },
    },
  };

  try {
    logger.info("Running ruff check on all Python files...");
    const ruffRes = await sandbox.process.executeCommand(
      `${RUN_PYTHON_IN_VENV} -m ruff check . --output-format=json`,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC * 3,
    );

    testResults.details.ruff.exitCode = ruffRes.exitCode;
    testResults.details.ruff.output = ruffRes.result;

    if (ruffRes.exitCode === 0) {
      testResults.ruffScore = 1;
      logger.info("Ruff analysis passed. No issues found.");
    } else {
      try {
        const ruffIssues = JSON.parse(ruffRes.result);
        testResults.details.ruff.issues = ruffIssues;

        const issueCount = Array.isArray(ruffIssues) ? ruffIssues.length : 0;
        testResults.ruffScore = issueCount === 0 ? 1 : 0; // Binary scoring: pass/fail

        logger.info(`Ruff found ${issueCount} issues`, {
          score: testResults.ruffScore,
          issues: ruffIssues.slice(0, 3), // Log first 3 issues
        });
      } catch (parseError) {
        testResults.ruffScore = 0;
        logger.warn(
          "Could not parse ruff JSON output. Setting Ruff score to 0.",
          {
            parseError,
            output: ruffRes.result?.substring(0, 200) + "...",
          },
        );
      }
    }
  } catch (error) {
    logger.error("Failed to run ruff check", { error });
    testResults.details.ruff.error = error;
    testResults.ruffScore = 0;
  }

  // Run mypy on all Python files in the repository
  try {
    logger.info("Running mypy type check on all Python files...");
    const mypyRes = await sandbox.process.executeCommand(
      `${RUN_PYTHON_IN_VENV} -m mypy . --no-error-summary --show-error-codes --no-color-output`,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC * 3,
    );

    testResults.details.mypy.exitCode = mypyRes.exitCode;
    testResults.details.mypy.output = mypyRes.result;

    if (mypyRes.exitCode === 0) {
      testResults.mypyScore = 1; // Perfect score if no issues
      logger.info("MyPy analysis passed - no type issues found");
    } else {
      const errorLines = mypyRes.result
        .split("\n")
        .filter(
          (line) => line.includes(": error:") || line.includes(": warning:"),
        );

      testResults.details.mypy.issues = errorLines;

      const issueCount = errorLines.length;
      testResults.mypyScore = issueCount === 0 ? 1 : 0; // Binary scoring: pass/fail

      logger.info(`MyPy found ${issueCount} issues`, {
        score: testResults.mypyScore,
        issues: errorLines.slice(0, 3),
      });
    }
  } catch (error) {
    logger.error("Failed to run mypy", { error });
    testResults.details.mypy.error = error;
    testResults.mypyScore = 0;
  }

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

  const sandbox = await daytonaInstance.create({
    image: SNAPSHOT_NAME,
  });

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
