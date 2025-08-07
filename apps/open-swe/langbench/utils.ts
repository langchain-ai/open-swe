import { Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "../src/utils/logger.js";
import { ENV_CONSTANTS } from "../src/utils/env-setup.js";

const logger = createLogger(LogLevel.DEBUG, "Langbench Utils");

/**
 * Fetch diff content from a diff URL and extract test file names
 */
export async function getTestFilesFromDiff(diffUrl: string): Promise<string[]> {
  try {
    const response = await fetch(diffUrl);
    if (!response.ok) {
      throw new Error(`Failed to fetch diff: ${response.statusText}`);
    }

    const diffContent = await response.text();
    const testFiles: string[] = [];

    // Parse the diff to find modified files
    const lines = diffContent.split("\n");
    for (const line of lines) {
      // Look for diff file headers
      if (line.startsWith("diff --git ")) {
        // Extract file path from "diff --git a/path/to/file.py b/path/to/file.py"
        const match = line.match(/diff --git a\/(.+?) b\//);
        if (match) {
          const filePath = match[1];
          // Check if this is a test file in libs/langgraph/tests/
          if (isLangGraphTestFile(filePath)) {
            testFiles.push(filePath);
          }
        }
      }
    }

    return [...new Set(testFiles)]; // Remove duplicates
  } catch (error) {
    logger.error(`Failed to fetch or parse diff from ${diffUrl}:`, { error });
    return [];
  }
}

/**
 * Check if a file path represents a test file in libs/langgraph/tests/
 */
export function isLangGraphTestFile(filePath: string): boolean {
  return filePath.includes("libs/langgraph/tests/") && filePath.endsWith(".py");
}

// Use shared constants from env-setup utility
const { RUN_PYTHON_IN_VENV, RUN_PIP_IN_VENV } = ENV_CONSTANTS;

// Installation commands for pytest and dependencies
const PYTEST_INSTALL_COMMANDS = [
  `${RUN_PIP_IN_VENV} install pytest pytest-mock pytest-asyncio syrupy pytest-json-report`,
  `${RUN_PIP_IN_VENV} install -e ./libs/langgraph`,
];

export interface TestResult {
  success: boolean;
  error: string | null;
  totalTests: number;
  passedTests: number;
  failedTests: number;
  testDetails: string[];
}

export interface ExecOptions {
  command: string;
  workingDir: string;
  env?: Record<string, string>;
  timeoutSec: number;
}

/**
 * Run pytest on specific test files and return structured results
 */
export const runPytestOnFiles = async (
  sandbox: Sandbox,
  testFiles: string[],
  repoDir: string,
  timeoutSec: number = 300,
): Promise<TestResult> => {
  if (testFiles.length === 0) {
    logger.info("No test files provided, skipping pytest execution");
    return {
      success: true,
      error: null,
      totalTests: 0,
      passedTests: 0,
      failedTests: 0,
      testDetails: [],
    };
  }

  logger.info(`Running pytest on ${testFiles.length} test files`, {
    testFiles,
  });

  // Join test files for pytest command
  const testFilesArg = testFiles.join(" ");
  const command = `${RUN_PYTHON_IN_VENV} -m pytest ${testFilesArg} -v --tb=short --json-report --json-report-file=/tmp/pytest_report.json`;
  logger.info("Running pytest command", { command });

  logger.info(
    "Installing pytest, pytest-mock, pytest-asyncio, syrupy, pytest-json-report, and langgraph in virtual environment...",
  );
  const installCommand = PYTEST_INSTALL_COMMANDS.join(" && ");
  const installResult = await sandbox.process.executeCommand(
    installCommand,
    repoDir,
    undefined,
    timeoutSec * 2,
  );

  logger.info("Installation completed", {
    exitCode: installResult.exitCode,
    output: installResult.result?.slice(0, 500),
  });

  try {
    const execution = await sandbox.process.executeCommand(
      command,
      repoDir,
      undefined,
      timeoutSec,
    );

    // Read the JSON report file
    let parsed: Omit<TestResult, "success" | "error">;
    try {
      const jsonReportResult = await sandbox.process.executeCommand(
        "cat /tmp/pytest_report.json",
        repoDir,
        undefined,
        30,
      );

      if (jsonReportResult.exitCode === 0 && jsonReportResult.result) {
        const jsonReport = JSON.parse(jsonReportResult.result);
        parsed = parsePytestJsonReport(jsonReport);
        logger.debug("Successfully parsed JSON report", { jsonReport });
      } else {
        logger.warn("Failed to read JSON report, falling back to text parsing");
        const output = execution.result || "";
        parsed = parsePytestOutput(output);
      }
    } catch (jsonError) {
      logger.warn("Failed to parse JSON report, falling back to text parsing", {
        jsonError,
      });
      const output = execution.result || "";
      parsed = parsePytestOutput(output);
    }

    logger.info("Pytest execution completed", {
      exitCode: execution.exitCode,
      totalTests: parsed.totalTests,
      passedTests: parsed.passedTests,
      failedTests: parsed.failedTests,
      command,
      stdout: execution.result,
      fullExecution: JSON.stringify(execution, null, 2), // Show full execution object
    });

    return {
      success: execution.exitCode === 0,
      error:
        execution.exitCode !== 0 ? `Exit code: ${execution.exitCode}` : null,
      ...parsed,
    };
  } catch (error) {
    logger.error("Failed to run pytest", { error });
    return {
      success: false,
      error: error instanceof Error ? error.message : String(error),
      totalTests: 0,
      passedTests: 0,
      failedTests: 0,
      testDetails: [],
    };
  }
};

/**
 * Parse pytest JSON report to extract test results
 */
export const parsePytestJsonReport = (
  jsonReport: any,
): Omit<TestResult, "success" | "error"> => {
  let totalTests = 0;
  let passedTests = 0;
  let failedTests = 0;
  const testDetails: string[] = [];

  if (jsonReport && jsonReport.tests) {
    totalTests = jsonReport.tests.length;

    for (const test of jsonReport.tests) {
      const testName = `${test.nodeid}`;
      const outcome = test.outcome;

      if (outcome === "passed") {
        passedTests++;
        testDetails.push(`${testName} PASSED`);
      } else if (outcome === "failed" || outcome === "error") {
        failedTests++;
        testDetails.push(`${testName} ${outcome.toUpperCase()}`);
      }
    }
  }

  // Use summary data if available
  if (jsonReport && jsonReport.summary) {
    const summary = jsonReport.summary;
    if (summary.passed !== undefined) passedTests = summary.passed;
    if (summary.failed !== undefined) failedTests = summary.failed;
    if (summary.error !== undefined) failedTests += summary.error;
    totalTests = passedTests + failedTests;
  }

  logger.debug("Parsed pytest JSON report", {
    totalTests,
    passedTests,
    failedTests,
    detailsCount: testDetails.length,
  });

  return {
    totalTests,
    passedTests,
    failedTests,
    testDetails,
  };
};

/**
 * Fallback: Parse pytest text output to extract test results (legacy)
 */
export const parsePytestOutput = (
  output: string,
): Omit<TestResult, "success" | "error"> => {
  const lines = output.split("\n");
  let totalTests = 0;
  let passedTests = 0;
  let failedTests = 0;
  const testDetails: string[] = [];

  // Collect test details and count results from individual test lines
  for (const line of lines) {
    if (
      line.includes("::") &&
      (line.includes("PASSED") ||
        line.includes("FAILED") ||
        line.includes("ERROR"))
    ) {
      testDetails.push(line.trim());

      if (line.includes("PASSED")) {
        passedTests++;
      } else if (line.includes("FAILED") || line.includes("ERROR")) {
        failedTests++;
      }
    }
  }

  totalTests = passedTests + failedTests;

  return {
    totalTests,
    passedTests,
    failedTests,
    testDetails,
  };
};
