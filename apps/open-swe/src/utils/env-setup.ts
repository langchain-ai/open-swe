import { Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "./logger.js";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";

const logger = createLogger(LogLevel.INFO, "EnvSetup");

const VENV_PATH = ".venv";
const RUN_PYTHON_IN_VENV = `${VENV_PATH}/bin/python`;
const RUN_PIP_IN_VENV = `${VENV_PATH}/bin/pip`;

/**
 * Setup Python environment with requirements.txt + ruff + mypy
 */
export async function setupEnv(
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
 * Export the constants for use in other files
 */
export const ENV_CONSTANTS = {
  VENV_PATH,
  RUN_PYTHON_IN_VENV,
  RUN_PIP_IN_VENV,
};
