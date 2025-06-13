import "dotenv/config";
import { SWEBenchInput } from "./swe-bench-types.js";
import { Daytona, Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "../src/utils/logger.js";
import { SNAPSHOT_NAME, TIMEOUT_SEC } from "@open-swe/shared/constants";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { cloneRepo, getRepoAbsolutePath } from "../src/utils/git.js";
import { SimpleEvaluationResult } from "langsmith/vitest";

const logger = createLogger(LogLevel.INFO, "Evaluator-RunTests");

const VENV_PATH = ".venv";
const RUN_PYTHON_IN_VENV = `${VENV_PATH}/bin/python`;
const RUN_PIP_IN_VENV = `${VENV_PATH}/bin/pip`;

const INSTALL_COMMANDS_REPO_MAP: Record<string, string[]> = {
  // Does not work
  pyvista: [
    `${RUN_PYTHON_IN_VENV} -m pip install -e .`,
    `${RUN_PIP_IN_VENV} install -r requirements_test.txt`,
    `${RUN_PIP_IN_VENV} install -r requirements_docs.txt`,
  ],
  // Works
  sqlfluff: [
    `${RUN_PYTHON_IN_VENV} -m pip install -U tox`,
    `${RUN_PIP_IN_VENV} install -U .`,
  ],
  // Does not work
  pydicom: [
    `${RUN_PIP_IN_VENV} install -e .`,
    `${RUN_PIP_IN_VENV} install numpy pillow pytest`,
  ],
  // Works
  astroid: [
    `${RUN_PIP_IN_VENV} install -e .`,
    `${RUN_PIP_IN_VENV} install -e . pytest`,
  ],
};

async function setupEnv(
  sandbox: Sandbox,
  absoluteRepoDir: string,
  repoName: string,
): Promise<boolean> {
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

  // 3. Install dependencies
  if (!(repoName in INSTALL_COMMANDS_REPO_MAP)) {
    logger.error("No install commands found for repo", {
      repoName,
    });
    return false;
  }

  let dependenciesInstalled = false;
  const installDepsCommands = INSTALL_COMMANDS_REPO_MAP[repoName];
  const installDepsTimeout = TIMEOUT_SEC * 3;

  for await (const installCommand of installDepsCommands) {
    try {
      const installDependenciesRes = await sandbox.process.executeCommand(
        installCommand,
        absoluteRepoDir,
        undefined,
        installDepsTimeout,
      );
      if (installDependenciesRes.exitCode !== 0) {
        throw new Error(
          `Failed to install dependencies\nCommand: ${installCommand}\n${installDependenciesRes.result}\nExit code: ${installDependenciesRes.exitCode}`,
        );
      }
      logger.info(
        `Successfully installed dependencies using command: ${installCommand}`,
      );
      dependenciesInstalled = true;
    } catch (e) {
      logger.error("Failed to install dependencies", {
        e,
        installCommand,
      });
    }
  }

  return dependenciesInstalled;
}

async function runTests(
  sandbox: Sandbox,
  absoluteRepoDir: string,
  tests: string[],
  type: "pass_to_pass" | "fail_to_pass",
) {
  let numPassed = 0;
  let numFailed = 0;

  for (const test of tests) {
    try {
      const runTestRes = await sandbox.process.executeCommand(
        `${RUN_PYTHON_IN_VENV} -m pytest ${test}`,
        absoluteRepoDir,
        undefined,
        TIMEOUT_SEC,
      );

      if (
        runTestRes.exitCode !== 0 &&
        !runTestRes.result.includes("no tests ran")
      ) {
        logger.error(`Failed to run ${type} test`, {
          test,
          runTestRes,
        });
        numFailed += 1;
      } else {
        numPassed += 1;
      }
    } catch (e) {
      logger.error(`Failed to run ${type} test`, {
        test,
        error: e,
      });
      numFailed += 1;
    }
  }

  if (numFailed === 0) {
    logger.info(`✅ All ${type} tests passed! ✅`);
    return 1;
  }
  logger.info(
    `❌ ${numFailed}/${numFailed + numPassed} ${type} tests failed! ❌`,
  );
  return numFailed / (numFailed + numPassed);
}

export async function evaluator(inputs: {
  sweBenchInputs: SWEBenchInput;
  output: GraphState;
}): Promise<SimpleEvaluationResult[]> {
  /**
   * Start a new sandbox instance
   * Checkout the env setup commit
   * Setup env
   * Checkout the generated branch
   * Apply test patch
   * Run pass to pass tests - log outputs
   * Run fail to pass tests - log outputs
   * Return score
   *  1. if any pass to pass tests fail, return 0
   *  2. if all pass to pass tests pass, return the percentage of fail to pass tests that passed.
   *    e.g. if 1/2 fail to pass tests pass, return 0.5
   */
  const { sweBenchInputs, output } = inputs;

  const githubToken = process.env.GITHUB_PAT;
  if (!githubToken) {
    throw new Error("GITHUB_PAT environment variable is not set");
  }

  const daytonaInstance = new Daytona();
  logger.info("Creating sandbox...");
  const sandbox = await daytonaInstance.create({
    image: SNAPSHOT_NAME,
  });

  try {
    const res = await cloneRepo(sandbox, output.targetRepository, {
      githubAccessToken: githubToken,
    });
    if (res.exitCode !== 0) {
      logger.error("Failed to clone repository", {
        targetRepository: output.targetRepository,
        cloneResult: res,
      });
      throw new Error("Failed to clone repository");
    }

    // Checkout env setup commit
    const absoluteRepoDir = getRepoAbsolutePath(output.targetRepository);
    const checkoutEnvCommitRes = await sandbox.process.executeCommand(
      `git checkout ${sweBenchInputs.environment_setup_commit}`,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );
    if (checkoutEnvCommitRes.exitCode !== 0) {
      logger.error("Failed to checkout env setup commit", {
        envSetupCommit: sweBenchInputs.environment_setup_commit,
        checkoutResult: checkoutEnvCommitRes,
      });
      throw new Error("Failed to checkout env setup commit");
    }

    const envSetupRes = await setupEnv(
      sandbox,
      absoluteRepoDir,
      output.targetRepository.repo,
    );
    if (!envSetupRes) {
      logger.error("Failed to setup environment");
      throw new Error("Failed to setup environment");
    }

    // Checkout the generated branch
    const checkoutGeneratedBranchRes = await sandbox.process.executeCommand(
      `git checkout ${output.branchName}`,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );
    if (checkoutGeneratedBranchRes.exitCode !== 0) {
      logger.error("Failed to checkout generated branch", {
        generatedBranch: output.branchName,
        checkoutResult: checkoutGeneratedBranchRes,
      });
      throw new Error("Failed to checkout generated branch");
    }

    // Apply test patch
    const escapedDiff = sweBenchInputs.test_patch.replace(/EOF/g, "E0F");
    const gitApplyCommand = `git apply << 'EOF'\n${escapedDiff}\nEOF`;

    const applyPatchRes = await sandbox.process.executeCommand(
      gitApplyCommand,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );
    if (applyPatchRes.exitCode !== 0) {
      logger.error("Failed to apply test patch", {
        testPatch: sweBenchInputs.test_patch,
        applyPatchResult: applyPatchRes,
      });
      throw new Error("Failed to apply test patch");
    }

    let passToPassTests: string[] = [];
    let failToPassTests: string[] = [];
    try {
      passToPassTests = JSON.parse(sweBenchInputs.PASS_TO_PASS);
      if (
        !Array.isArray(passToPassTests) ||
        passToPassTests.some((t) => typeof t !== "string")
      ) {
        logger.error("Pass to pass tests must be an array of strings", {
          pass_to_pass_string: sweBenchInputs.PASS_TO_PASS,
          parsed_pass_to_pass_tests: passToPassTests,
        });
        throw new Error("Pass to pass tests must be an array of strings");
      }
    } catch (e) {
      logger.error("Failed to parse pass to pass tests string", {
        pass_to_pass_string: sweBenchInputs.PASS_TO_PASS,
        error: e,
      });
    }
    try {
      failToPassTests = JSON.parse(sweBenchInputs.FAIL_TO_PASS);
      if (
        !Array.isArray(failToPassTests) ||
        failToPassTests.some((t) => typeof t !== "string")
      ) {
        logger.error("Fail to pass tests must be an array of strings", {
          fail_to_pass_string: sweBenchInputs.FAIL_TO_PASS,
          parsed_fail_to_pass_tests: failToPassTests,
        });
        throw new Error("Fail to pass tests must be an array of strings");
      }
    } catch (e) {
      logger.error("Failed to parse fail to pass tests string", {
        fail_to_pass_string: sweBenchInputs.FAIL_TO_PASS,
        error: e,
      });
    }

    const passToPassScore = await runTests(
      sandbox,
      absoluteRepoDir,
      passToPassTests,
      "pass_to_pass",
    );
    const failToPassScore = await runTests(
      sandbox,
      absoluteRepoDir,
      failToPassTests,
      "fail_to_pass",
    );

    return [
      {
        key: "overall-score",
        score: (passToPassScore + failToPassScore) / 2,
      },
      {
        key: "pass-to-pass-score",
        score: passToPassScore,
      },
      {
        key: "fail-to-pass-score",
        score: failToPassScore,
      },
    ];
  } finally {
    logger.info("Evaluator completed, deleting sandbox.");
    await sandbox.delete();
  }
}
