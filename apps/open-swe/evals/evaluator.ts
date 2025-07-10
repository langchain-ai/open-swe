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
 * Simplified approach that works for any Python repository
 */
async function setupEnv(
  sandbox: Sandbox,
  absoluteRepoDir: string,
): Promise<boolean> {
  logger.info("Setting up Python environment...");

  // 1. Create the virtual environment
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

  // 2. Upgrade pip first
  const upgradePipRes = await sandbox.process.executeCommand(
    `${RUN_PIP_IN_VENV} install --upgrade pip`,
    absoluteRepoDir,
    undefined,
    TIMEOUT_SEC,
  );
  if (upgradePipRes.exitCode !== 0) {
    logger.warn("Failed to upgrade pip, continuing anyway", { upgradePipRes });
  }

  // 3. Install repository requirements if requirements.txt exists
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
      TIMEOUT_SEC * 3, // Longer timeout for requirements
    );
    if (installReqRes.exitCode !== 0) {
      logger.warn("Failed to install requirements.txt, continuing anyway", { installReqRes });
    }
  } else {
    logger.info("No requirements.txt found, skipping repository dependencies");
  }

  // 4. Install code analysis tools (ruff + mypy) - this must succeed
  const installAnalysisToolsRes = await sandbox.process.executeCommand(
    `${RUN_PIP_IN_VENV} install ruff mypy`,
    absoluteRepoDir,
    undefined,
    TIMEOUT_SEC,
  );
  if (installAnalysisToolsRes.exitCode !== 0) {
    logger.error("Failed to install ruff and mypy", { installAnalysisToolsRes });
    return false;
  }

  logger.info("Environment setup completed successfully");
  return true;
}

/**
 * Run ruff and mypy analysis on all Python files in the repository
 */
async function runCodeAnalysis(
  sandbox: Sandbox,
  absoluteRepoDir: string,
): Promise<{ ruffScore: number; mypyScore: number; details: any }> {
  logger.info("Running code analysis on all Python files in repository");
  
  const analysisResults = {
    ruffScore: 0,
    mypyScore: 0,
    details: {
      ruff: { issues: [] as any[], exitCode: -1, output: "", error: null as any },
      mypy: { issues: [] as string[], exitCode: -1, output: "", error: null as any }
    }
  };

  // Run ruff on all Python files in the repository
  try {
    logger.info("Running ruff analysis...");
    const ruffRes = await sandbox.process.executeCommand(
      `${RUN_PYTHON_IN_VENV} -m ruff check . --output-format=json`,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );
    
    analysisResults.details.ruff.exitCode = ruffRes.exitCode;
    analysisResults.details.ruff.output = ruffRes.result;
    
    if (ruffRes.exitCode === 0) {
      analysisResults.ruffScore = 1; // Perfect score if no issues
      logger.info("Ruff analysis passed - no issues found");
    } else {
      // Try to parse JSON output to count issues
      try {
        const ruffIssues = JSON.parse(ruffRes.result);
        analysisResults.details.ruff.issues = ruffIssues;
        
        const issueCount = Array.isArray(ruffIssues) ? ruffIssues.length : 0;
        analysisResults.ruffScore = issueCount === 0 ? 1 : 0; // Binary scoring: pass/fail
        
        logger.info(`Ruff found ${issueCount} issues`, { 
          score: analysisResults.ruffScore,
          sampleIssues: ruffIssues.slice(0, 3) // Log first 3 issues
        });
      } catch (parseError) {
        // If JSON parsing fails, use simple binary scoring
        analysisResults.ruffScore = 0;
        logger.warn("Could not parse ruff JSON output, using binary scoring", { 
          parseError, 
          output: ruffRes.result?.substring(0, 200) + "..." 
        });
      }
    }
  } catch (error) {
    logger.error("Failed to run ruff analysis", { error });
    analysisResults.details.ruff.error = error;
    analysisResults.ruffScore = 0;
  }

  // Run mypy on all Python files in the repository
  try {
    logger.info("Running mypy analysis...");
    const mypyRes = await sandbox.process.executeCommand(
      `${RUN_PYTHON_IN_VENV} -m mypy . --ignore-missing-imports --no-error-summary`,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );
    
    analysisResults.details.mypy.exitCode = mypyRes.exitCode;
    analysisResults.details.mypy.output = mypyRes.result;
    
    if (mypyRes.exitCode === 0) {
      analysisResults.mypyScore = 1; // Perfect score if no issues
      logger.info("MyPy analysis passed - no type issues found");
    } else {
      // Count mypy errors from output
      const errorLines = mypyRes.result.split('\n').filter(line => 
        line.includes(': error:') || line.includes(': warning:')
      );
      
      analysisResults.details.mypy.issues = errorLines;
      
      const issueCount = errorLines.length;
      analysisResults.mypyScore = issueCount === 0 ? 1 : 0; // Binary scoring: pass/fail
      
      logger.info(`MyPy found ${issueCount} issues`, { 
        score: analysisResults.mypyScore,
        sampleIssues: errorLines.slice(0, 3) // Log first 3 issues
      });
    }
  } catch (error) {
    logger.error("Failed to run mypy analysis", { error });
    analysisResults.details.mypy.error = error;
    analysisResults.mypyScore = 0;
  }

  return analysisResults;
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
     originalBranch: openSWEInputs.branch, // Branch the agent was asked to fix
     solutionBranch: output.branchName,    // Branch the agent created with solution
     user_input: openSWEInputs.user_input.substring(0, 100) + "..."
   });
  
  const sandbox = await daytonaInstance.create({
    image: SNAPSHOT_NAME,
  });

  try {
    // Clone the repository
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

    // Checkout the agent's solution branch (this contains their changes)
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

    // Setup Python environment
    const envSetupSuccess = await setupEnv(sandbox, absoluteRepoDir);
    if (!envSetupSuccess) {
      logger.error("Failed to setup environment");
      return [{
        key: "overall-score",
        score: 0,
      }];
    }

    // Run code analysis on all Python files
    const analysisResult = await runCodeAnalysis(sandbox, absoluteRepoDir);
    
    // Simple addition scoring (no weighting)
    const overallScore = analysisResult.ruffScore + analysisResult.mypyScore;
    
    logger.info("Evaluation completed", {
      overallScore,
      ruffScore: analysisResult.ruffScore,
      mypyScore: analysisResult.mypyScore,
      repo: openSWEInputs.repo,
      originalBranch: openSWEInputs.branch,
      solutionBranch: output.branchName
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
    return [{
      key: "overall-score",
      score: 0,
    }];
  } finally {
    // Cleanup sandbox
    try {
      await sandbox.delete();
      logger.info("Sandbox cleaned up successfully");
    } catch (cleanupError) {
      logger.error("Failed to cleanup sandbox", { cleanupError });
    }
  }
}