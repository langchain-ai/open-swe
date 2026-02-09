import "dotenv/config";
import { OpenSWEInput, CodeTestDetails } from "./open-swe-types.js";
import { Daytona, Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "../src/utils/logger.js";
import { TIMEOUT_SEC } from "@openswe/shared/constants";
import { DEFAULT_SANDBOX_CREATE_PARAMS } from "../src/constants.js";
import { TargetRepository } from "@openswe/shared/open-swe/types";
import { cloneRepo } from "../src/utils/github/git.js";
import { getRepoAbsolutePath } from "@openswe/shared/git";
import { SimpleEvaluationResult } from "langsmith/vitest";
import {
  runRuffLint,
  runMyPyTypeCheck,
  runLangGraphEvaluation,
} from "./tests.js";
import { writeFile } from "../src/utils/read-write.js";
import * as fs from "fs";
import * as path from "path";

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
  envVars?: Record<string, string>,
): Promise<boolean> {
  logger.info("Setting up Python environment...");

  const createVenvCommand = "python -m venv .venv";
  const createVenvRes = await sandbox.process.executeCommand(
    createVenvCommand,
    absoluteRepoDir,
    envVars,
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
    envVars,
    TIMEOUT_SEC,
  );
  if (upgradePipRes.exitCode !== 0) {
    logger.warn("Failed to upgrade pip, continuing anyway", { upgradePipRes });
  }

  const requirementsExistRes = await sandbox.process.executeCommand(
    "test -f requirements.txt",
    absoluteRepoDir,
    envVars,
    TIMEOUT_SEC,
  );

  if (requirementsExistRes.exitCode === 0) {
    logger.info("Found requirements.txt, installing...");
    const installReqRes = await sandbox.process.executeCommand(
      `${RUN_PIP_IN_VENV} install -r requirements.txt`,
      absoluteRepoDir,
      envVars,
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

  // Install evaluation-specific dependencies
  logger.info("Installing evaluation dependencies...");
  const installEvalDepsRes = await sandbox.process.executeCommand(
    `${RUN_PIP_IN_VENV} install langchain langchain-core langchain-openai pydantic openai`,
    absoluteRepoDir,
    envVars,
    TIMEOUT_SEC * 2,
  );
  if (installEvalDepsRes.exitCode !== 0) {
    logger.warn(
      "Failed to install evaluation dependencies, continuing anyway",
      {
        installEvalDepsRes,
      },
    );
  }

  const installAnalysisToolsRes = await sandbox.process.executeCommand(
    `${RUN_PIP_IN_VENV} install ruff mypy`,
    absoluteRepoDir,
    envVars,
    TIMEOUT_SEC,
  );
  if (installAnalysisToolsRes.exitCode !== 0) {
    logger.error("Failed to install ruff and mypy", {
      installAnalysisToolsRes,
    });
    return false;
  }

  logger.info("Copying LangGraph evaluation script to sandbox...");
  try {
    const evalScriptPath = path.join(
      __dirname,
      "scripts",
      "langgraph_check.py",
    );
    const evalScriptContent = fs.readFileSync(evalScriptPath, "utf8");

    const { success: copyScriptSuccess, output: copyScriptOutput } =
      await writeFile({
        sandbox,
        filePath: "langgraph_check.py",
        content: evalScriptContent,
        workDir: absoluteRepoDir,
      });

    if (!copyScriptSuccess) {
      logger.warn("Failed to copy LangGraph evaluation script", {
        copyScriptOutput,
      });
    } else {
      logger.info("Successfully copied LangGraph evaluation script to sandbox");
    }
  } catch (error) {
    logger.warn("Error copying LangGraph evaluation script", { error });
  }

  logger.info("Environment setup completed successfully");
  return true;
}

/**
 * Runs ruff and mypy analysis on all Python files in the repository
 */
async function runCodeTests(
  sandbox: Sandbox,
  absoluteRepoDir: string,
  openSWEInputs: OpenSWEInput,
  envVars?: Record<string, string>,
): Promise<{
  ruffScore: number;
  mypyScore: number;
  langGraphScore: number;
  details: CodeTestDetails;
}> {
  logger.info("Running code analysis on all Python files in repository");

  const testResults: {
    ruffScore: number;
    mypyScore: number;
    langGraphScore: number;
    details: CodeTestDetails;
  } = {
    ruffScore: 0,
    mypyScore: 0,
    langGraphScore: 0,
    details: {
      ruff: {
        issues: [],
        error: null,
      },
      mypy: {
        issues: [],
        error: null,
      },
      langGraph: {
        explanation: "",
        error: null,
      },
    },
  };

  const [ruffLint, mypyCheck, langGraphEval] = await Promise.all([
    runRuffLint(sandbox, {
      command: `${RUN_PYTHON_IN_VENV} -m ruff check . --output-format=json`,
      workingDir: absoluteRepoDir,
      env: envVars,
      timeoutSec: TIMEOUT_SEC * 3,
    }),
    runMyPyTypeCheck(sandbox, {
      command: `${RUN_PYTHON_IN_VENV} -m mypy . --no-error-summary --show-error-codes --no-color-output`,
      workingDir: absoluteRepoDir,
      env: envVars,
      timeoutSec: TIMEOUT_SEC * 3,
    }),
    runLangGraphEvaluation(sandbox, {
      command: `${RUN_PYTHON_IN_VENV} -m langgraph_check agent.py -i "${openSWEInputs.test_input}" -g "${openSWEInputs.ground_truth}"`,
      workingDir: absoluteRepoDir,
      env: envVars,
      timeoutSec: TIMEOUT_SEC * 3,
    }),
  ]);

  Object.assign(testResults, {
    ruffScore: ruffLint.ruffScore,
    mypyScore: mypyCheck.mypyScore,
    langGraphScore: langGraphEval.langGraphScore,
    details: {
      ruff: {
        issues: ruffLint.issues,
        error: ruffLint.error,
      },
      mypy: {
        issues: mypyCheck.issues,
        error: mypyCheck.error,
      },
      langGraph: {
        explanation: langGraphEval.explanation,
        error: langGraphEval.error,
      },
    },
  });

  logger.info("Code tests completed", {
    ruffScore: testResults.ruffScore,
    mypyScore: testResults.mypyScore,
    langGraphScore: testResults.langGraphScore,
    ruffIssues: testResults.details.ruff.issues.length,
    mypyIssues: testResults.details.mypy.issues.length,
    langGraphExplanation: testResults.details.langGraph.explanation,
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

    const envVars: Record<string, string> = {};
    const apiKeys = [
      "OPENAI_API_KEY",
      "ANTHROPIC_API_KEY",
      "LANGCHAIN_API_KEY",
      "LANGCHAIN_TRACING_V2",
      "LANGCHAIN_PROJECT",
      "GOOGLE_API_KEY",
      "TAVILY_API_KEY",
    ];

    apiKeys.forEach((key) => {
      if (process.env[key]) {
        envVars[key] = process.env[key]!;
        logger.info(`Added environment variable: ${key}`);
      }
    });

    logger.info(
      `Syncing to latest state of solution branch: ${solutionBranch}`,
    );

    const updateBranchRes = await sandbox.process.executeCommand(
      `git fetch origin ${solutionBranch} && git checkout -B ${solutionBranch} FETCH_HEAD`,
      absoluteRepoDir,
      envVars,
      TIMEOUT_SEC,
    );

    if (updateBranchRes.exitCode !== 0) {
      logger.error("Failed to update solution branch", {
        solutionBranch,
        updateResult: updateBranchRes,
      });
      throw new Error(`Failed to update solution branch: ${solutionBranch}`);
    }

    logger.info("Git update result:", {
      exitCode: updateBranchRes.exitCode,
      result: updateBranchRes.result,
    });

    const envSetupSuccess = await setupEnv(sandbox, absoluteRepoDir, envVars);
    if (!envSetupSuccess) {
      logger.error("Failed to setup environment");
      return [
        {
          key: "overall-score",
          score: 0,
        },
      ];
    }

    const analysisResult = await runCodeTests(
      sandbox,
      absoluteRepoDir,
      openSWEInputs,
      envVars,
    );

    const overallScore =
      analysisResult.ruffScore +
      analysisResult.mypyScore +
      analysisResult.langGraphScore;

    logger.info("Evaluation completed", {
      overallScore,
      ruffScore: analysisResult.ruffScore,
      mypyScore: analysisResult.mypyScore,
      langGraphScore: analysisResult.langGraphScore,
      langGraphExplanation: analysisResult.details.langGraph.explanation,
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
        comment: analysisResult.details.ruff.issues.join("\n"),
      },
      {
        key: "mypy-score",
        score: analysisResult.mypyScore,
        comment: analysisResult.details.mypy.issues.join("\n"),
      },
      {
        key: "langgraph-score",
        score: analysisResult.langGraphScore,
        comment: analysisResult.details.langGraph.explanation,
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
