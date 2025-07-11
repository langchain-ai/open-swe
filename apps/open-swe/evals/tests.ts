// TODO: Add ruff promise and the mypy promise to the tests.
import { Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "../src/utils/logger.js";
import {
  ExecOptions,
  RuffResult,
  RuffIssue,
  MyPyResult,
} from "./open-swe-types.js";

const logger = createLogger(LogLevel.DEBUG, " Evaluation Tests");

/**
 * Run ruff check and return score, error, and issues
 */
export const runRuffLint = async (
  sandbox: Sandbox,
  args: ExecOptions,
): Promise<RuffResult> => {
  logger.info("Running ruff check...");

  try {
    const execution = await sandbox.process.executeCommand(
      args.command,
      args.workingDir,
      args.env,
      args.timeoutSec,
    );

    if (execution.exitCode === 0) {
      logger.info("Ruff analysis passed. No issues found.");
      return {
        ruffScore: 1,
        error: null,
        issues: [],
      };
    }

    try {
      const issues: RuffIssue[] = JSON.parse(execution.result);
      const issueCount = Array.isArray(issues) ? issues.length : 0;
      const ruffScore = issueCount === 0 ? 1 : 0;

      logger.info(`Ruff found ${issueCount} issues`, {
        score: ruffScore,
        issues: issues.slice(0, 3), // Log first 3 issues
      });

      return {
        ruffScore,
        error: null,
        issues,
      };
    } catch (parseError) {
      logger.warn(
        "Could not parse ruff JSON output. Setting Ruff score to 0.",
        {
          parseError,
          output: execution.result?.substring(0, 200) + "...",
        },
      );

      return {
        ruffScore: 0,
        error: parseError as Error,
        issues: [],
      };
    }
  } catch (error) {
    logger.error("Failed to run ruff check", { error });
    return {
      ruffScore: 0,
      error: error as Error,
      issues: [],
    };
  }
};

/**
 * Run mypy check and return score, error, and issues
 */
export const runMyPyTypeCheck = async (
  sandbox: Sandbox,
  args: ExecOptions,
): Promise<MyPyResult> => {
  logger.info("Running mypy check...");
  try {
    const execution = await sandbox.process.executeCommand(
      args.command,
      args.workingDir,
      args.env,
      args.timeoutSec,
    );

    if (execution.exitCode === 0) {
      logger.info("Mypy analysis passed. No issues found.");
      return {
        mypyScore: 1,
        error: null,
        issues: [],
      };
    } else {
      // Filter for actual type problems: errors and warnings
      const errorLines = execution.result
        .split("\n")
        .filter(
          (line) => line.includes(": error:") || line.includes(": warning:"),
        );

      const issueCount = errorLines.length;
      const mypyScore = issueCount === 0 ? 1 : 0;

      logger.info(`Mypy found ${issueCount} issues`, {
        score: mypyScore,
        issues: errorLines.slice(0, 3),
      });

      return {
        mypyScore,
        error: null,
        issues: errorLines,
      };
    }
  } catch (error) {
    logger.error("Failed to run mypy check", { error });
    return {
      mypyScore: 0,
      error: error as Error,
      issues: [],
    };
  }
};
