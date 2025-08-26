import { createLogger, LogLevel } from "../src/utils/logger.js";
import { ENV_CONSTANTS } from "../src/utils/env-setup.js";
import { TestResults, PytestJsonReport, RunPytestOptions } from "./types.js";
import { readFile } from "../src/utils/read-write.js";

const logger = createLogger(LogLevel.DEBUG, "Langbench Utils");

/**
 * Fetch diff content from a diff URL and extract test file names, this function is used in one-off situtations to get the test files from the diff url.
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
function isLangGraphTestFile(filePath: string): boolean {
  return filePath.includes("libs/langgraph/tests/") && filePath.endsWith(".py");
}

// Use shared constants from env-setup utility
const { RUN_PYTHON_IN_VENV, RUN_PIP_IN_VENV } = ENV_CONSTANTS;

// Installation commands for pytest and dependencies
const PIP_INSTALL_COMMAND = `${RUN_PIP_IN_VENV} install pytest pytest-mock pytest-asyncio syrupy pytest-json-report`;
const LANGGRAPH_INSTALL_COMMAND = `${RUN_PIP_IN_VENV} install -e ./libs/langgraph`;

/**
 * Run pytest on specific test files and return structured results
 */
export async function runPytestOnFiles(
  options: RunPytestOptions,
): Promise<TestResults> {
  const { sandbox, testFiles, repoDir, timeoutSec = 300 } = options;
  if (testFiles.length === 0) {
    logger.warn("No test files provided, skipping pytest execution");
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

  // Execute pip install command
  logger.info(`Running pip install command: ${PIP_INSTALL_COMMAND}`);
  const pipInstallResult = await sandbox.process.executeCommand(
    PIP_INSTALL_COMMAND,
    repoDir,
    undefined,
    timeoutSec * 2,
  );

  logger.info(`Pip install command completed`, {
    exitCode: pipInstallResult.exitCode,
    output: pipInstallResult.result?.slice(0, 500),
  });

  if (pipInstallResult.exitCode !== 0) {
    logger.error(`Pip install command failed`, {
      command: PIP_INSTALL_COMMAND,
      exitCode: pipInstallResult.exitCode,
      output: pipInstallResult.result,
    });
  }

  // Execute langgraph install command
  logger.info(
    `Running langgraph install command: ${LANGGRAPH_INSTALL_COMMAND}`,
  );
  const langgraphInstallResult = await sandbox.process.executeCommand(
    LANGGRAPH_INSTALL_COMMAND,
    repoDir,
    undefined,
    timeoutSec * 2,
  );

  logger.info(`Langgraph install command completed`, {
    exitCode: langgraphInstallResult.exitCode,
    output: langgraphInstallResult.result?.slice(0, 500),
  });

  if (langgraphInstallResult.exitCode !== 0) {
    logger.error(`Langgraph install command failed`, {
      command: LANGGRAPH_INSTALL_COMMAND,
      exitCode: langgraphInstallResult.exitCode,
      output: langgraphInstallResult.result,
    });
  }

  try {
    const execution = await sandbox.process.executeCommand(
      command,
      repoDir,
      undefined,
      timeoutSec,
    );

    // Read the JSON report file
    let parsed: Omit<TestResults, "success" | "error">;
    try {
      const jsonReportResult = await readFile({
        config: {},
        sandbox,
        filePath: "/tmp/pytest_report.json",
        workDir: repoDir,
      });

      if (jsonReportResult.success && jsonReportResult.output) {
        const jsonReport = JSON.parse(jsonReportResult.output);
        parsed = parsePytestJsonReport(jsonReport);
        logger.debug("Successfully parsed JSON report", { jsonReport });
      } else {
        throw new Error(
          `Failed to read JSON report: ${jsonReportResult.output}`,
        );
      }
    } catch (jsonError) {
      throw new Error("Failed to parse JSON report", { cause: jsonError });
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
}

/**
 * Parse pytest JSON report to extract test results
 */
export function parsePytestJsonReport(
  jsonReport: PytestJsonReport,
): Omit<TestResults, "success" | "error"> {
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
}
