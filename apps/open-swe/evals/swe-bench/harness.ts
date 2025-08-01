import { spawn } from "child_process";
import * as fs from "fs/promises";
import * as path from "path";
import { createLogger, LogLevel } from "../../src/utils/logger.js";
import {
  SWEBenchPrediction,
  SWEBenchResult,
  SWEBenchHarnessOptions,
  SWEBenchMetrics,
  SWEBenchEvaluationReport,
} from "./types.js";

const logger = createLogger(LogLevel.INFO, "SWEBenchHarness");

/**
 * Default paths for SWE-bench evaluation
 */
const DEFAULT_PATHS = {
  PREDICTIONS_DIR: "swe-bench-predictions",
  RESULTS_DIR: "evaluation_results",
  LOGS_DIR: "logs",
};

/**
 * Writes predictions to a JSONL file for SWE-bench evaluation
 */
export async function writePredictionsToFile(
  predictions: SWEBenchPrediction[],
  filePath: string,
): Promise<void> {
  try {
    // Ensure directory exists
    const dir = path.dirname(filePath);
    await fs.mkdir(dir, { recursive: true });

    // Write each prediction as a JSON line
    const lines = predictions.map((pred) => JSON.stringify(pred));
    await fs.writeFile(filePath, lines.join("\n"), "utf-8");

    logger.info(`Wrote ${predictions.length} predictions to ${filePath}`);
  } catch (error) {
    logger.error("Failed to write predictions file", { error, filePath });
    throw new Error(`Failed to write predictions: ${error}`);
  }
}

/**
 * Checks if Docker is available and running
 */
export async function checkDockerAvailable(): Promise<boolean> {
  return new Promise((resolve) => {
    const dockerCheck = spawn("docker", ["--version"]);

    dockerCheck.on("close", (code: number | null) => {
      if (code === 0) {
        logger.info("Docker is available");
        resolve(true);
      } else {
        logger.warn("Docker check failed");
        resolve(false);
      }
    });

    dockerCheck.on("error", (error: Error) => {
      logger.error("Docker not found", { error });
      resolve(false);
    });
  });
}

/**
 * Checks if the SWE-bench Python package is installed
 */
export async function checkSWEBenchInstalled(): Promise<boolean> {
  return new Promise((resolve) => {
    const pythonCheck = spawn("python", ["-c", "import swebench"]);

    pythonCheck.on("close", (code: number | null) => {
      if (code === 0) {
        logger.info("SWE-bench Python package is installed");
        resolve(true);
      } else {
        logger.warn("SWE-bench Python package not found");
        resolve(false);
      }
    });

    pythonCheck.on("error", (error: Error) => {
      logger.error("Python or SWE-bench not found", { error });
      resolve(false);
    });
  });
}

/**
 * Runs the SWE-bench evaluation harness
 */
export async function runSWEBenchHarness(
  options: SWEBenchHarnessOptions,
): Promise<{ success: boolean; output: string; error?: string }> {
  const {
    dataset_name,
    predictions_path,
    max_workers = 8,
    run_id,
    cache_level = "env",
    timeout = 1800,
    instance_ids,
    clean = false,
    split = "test",
    force_rebuild = false,
    open_file_limit = 4096,
  } = options;

  // Build command arguments
  const args = [
    "-m",
    "swebench.harness.run_evaluation",
    "--dataset_name",
    dataset_name,
    "--predictions_path",
    predictions_path,
    "--max_workers",
    max_workers.toString(),
    "--run_id",
    run_id,
    "--cache_level",
    cache_level,
    "--timeout",
    timeout.toString(),
    "--split",
    split,
    "--open_file_limit",
    open_file_limit.toString(),
  ];

  if (instance_ids) {
    args.push("--instance_ids", instance_ids);
  }

  if (clean) {
    args.push("--clean", "True");
  }

  if (force_rebuild) {
    args.push("--force_rebuild", "True");
  }

  logger.info("Running SWE-bench harness", {
    command: "python",
    args,
  });

  return new Promise((resolve) => {
    const output: string[] = [];
    const errorOutput: string[] = [];

    const harnessProcess = spawn("python", args, {
      env: { ...process.env },
      cwd: process.cwd(),
    });

    harnessProcess.stdout.on("data", (data: Buffer) => {
      const text = data.toString();
      output.push(text);
      // Log important lines
      if (
        text.includes("Instances resolved:") ||
        text.includes("Instances unresolved:")
      ) {
        logger.info(text.trim());
      }
    });

    harnessProcess.stderr.on("data", (data: Buffer) => {
      const text = data.toString();
      errorOutput.push(text);
      logger.warn("Harness stderr:", text.trim());
    });

    harnessProcess.on("close", (code: number | null) => {
      const fullOutput = output.join("");
      const fullError = errorOutput.join("");

      if (code === 0) {
        logger.info("SWE-bench harness completed successfully");
        resolve({
          success: true,
          output: fullOutput,
        });
      } else {
        logger.error("SWE-bench harness failed", {
          code,
          error: fullError,
        });
        resolve({
          success: false,
          output: fullOutput,
          error: fullError || `Process exited with code ${code}`,
        });
      }
    });

    harnessProcess.on("error", (error: Error) => {
      logger.error("Failed to spawn harness process", { error });
      resolve({
        success: false,
        output: output.join(""),
        error: error.message,
      });
    });
  });
}

/**
 * Parses the results.json file from SWE-bench evaluation
 */
export async function parseResultsFile(
  resultsPath: string,
): Promise<SWEBenchMetrics | null> {
  try {
    const content = await fs.readFile(resultsPath, "utf-8");
    const data = JSON.parse(content);

    // Extract metrics from the results
    const metrics: SWEBenchMetrics = {
      total_instances: data.total_instances || 0,
      instances_submitted: data.instances_submitted || 0,
      instances_completed: data.instances_completed || 0,
      instances_resolved: data.instances_resolved || 0,
      resolution_rate: data.resolution_rate || 0,
      avg_fail_to_pass: data.avg_fail_to_pass || 0,
      avg_pass_to_pass: data.avg_pass_to_pass || 0,
      instances_with_errors: data.instances_with_errors || 0,
      instances_patch_failed: data.instances_patch_failed || 0,
    };

    return metrics;
  } catch (error) {
    logger.error("Failed to parse results file", { error, resultsPath });
    return null;
  }
}

/**
 * Parses the instance_results.jsonl file from SWE-bench evaluation
 */
export async function parseInstanceResults(
  instanceResultsPath: string,
): Promise<SWEBenchResult[]> {
  try {
    const content = await fs.readFile(instanceResultsPath, "utf-8");
    const lines = content
      .trim()
      .split("\n")
      .filter((line) => line.length > 0);

    const results: SWEBenchResult[] = [];

    for (const line of lines) {
      try {
        const data = JSON.parse(line);

        // Map the raw data to our SWEBenchResult interface
        const result: SWEBenchResult = {
          instance_id: data.instance_id,
          resolved: data.resolved || false,
          fail_to_pass: data.fail_to_pass || [],
          pass_to_pass: data.pass_to_pass || [],
          pass_to_fail: data.pass_to_fail || [],
          fail_to_fail: data.fail_to_fail || [],
          patch_successfully_applied: data.patch_successfully_applied || false,
          error: data.error,
          duration: data.duration,
        };

        results.push(result);
      } catch (parseError) {
        logger.warn("Failed to parse instance result line", {
          line,
          error: parseError,
        });
      }
    }

    return results;
  } catch (error) {
    logger.error("Failed to parse instance results file", {
      error,
      instanceResultsPath,
    });
    return [];
  }
}

/**
 * Finds the evaluation results directory for a given run
 */
export async function findResultsDirectory(
  runId: string,
  baseDir: string = DEFAULT_PATHS.RESULTS_DIR,
): Promise<string | null> {
  try {
    // Check if base directory exists
    await fs.access(baseDir);

    // Look for directories that might contain our run results
    const entries = await fs.readdir(baseDir, { withFileTypes: true });

    for (const entry of entries) {
      if (entry.isDirectory()) {
        // Check if this directory contains results for our run
        const potentialPath = path.join(baseDir, entry.name);
        const resultsJsonPath = path.join(potentialPath, "results.json");

        try {
          await fs.access(resultsJsonPath);
          // Check if this is our run by looking for run_id in the results
          const content = await fs.readFile(resultsJsonPath, "utf-8");
          if (content.includes(runId)) {
            return potentialPath;
          }
        } catch {
          // Continue searching
        }
      }
    }

    // Also check for a direct path with run_id
    const directPath = path.join(baseDir, runId);
    try {
      await fs.access(directPath);
      return directPath;
    } catch {
      // Not found
    }

    return null;
  } catch (error) {
    logger.error("Failed to find results directory", { error, runId, baseDir });
    return null;
  }
}

/**
 * Loads a complete evaluation report from the results
 */
export async function loadEvaluationReport(
  runId: string,
  modelName: string,
  datasetName: string,
  options: SWEBenchHarnessOptions,
  resultsDir?: string,
): Promise<SWEBenchEvaluationReport | null> {
  try {
    // Find results directory if not provided
    const actualResultsDir = resultsDir || (await findResultsDirectory(runId));

    if (!actualResultsDir) {
      logger.error("Could not find results directory", { runId });
      return null;
    }

    // Load metrics from results.json
    const resultsJsonPath = path.join(actualResultsDir, "results.json");
    const metrics = await parseResultsFile(resultsJsonPath);

    if (!metrics) {
      logger.error("Could not parse metrics", { resultsJsonPath });
      return null;
    }

    // Load instance results from instance_results.jsonl
    const instanceResultsPath = path.join(
      actualResultsDir,
      "instance_results.jsonl",
    );
    const instanceResults = await parseInstanceResults(instanceResultsPath);

    // Create the evaluation report
    const report: SWEBenchEvaluationReport = {
      run_id: runId,
      model: modelName,
      dataset: datasetName,
      started_at: new Date().toISOString(), // This would ideally come from logs
      completed_at: new Date().toISOString(),
      metrics,
      instance_results: instanceResults,
      config: options,
    };

    return report;
  } catch (error) {
    logger.error("Failed to load evaluation report", { error, runId });
    return null;
  }
}

/**
 * Cleans up evaluation artifacts
 */
export async function cleanupEvaluation(
  runId: string,
  options: {
    removePredictions?: boolean;
    removeResults?: boolean;
    removeLogs?: boolean;
  } = {},
): Promise<void> {
  const {
    removePredictions = false,
    removeResults = false,
    removeLogs = false,
  } = options;

  try {
    if (removePredictions) {
      const predictionsPath = path.join(
        DEFAULT_PATHS.PREDICTIONS_DIR,
        `${runId}.jsonl`,
      );
      try {
        await fs.unlink(predictionsPath);
        logger.info("Removed predictions file", { predictionsPath });
      } catch (error) {
        logger.warn("Could not remove predictions file", {
          error,
          predictionsPath,
        });
      }
    }

    if (removeResults) {
      const resultsDir = await findResultsDirectory(runId);
      if (resultsDir) {
        await fs.rm(resultsDir, { recursive: true, force: true });
        logger.info("Removed results directory", { resultsDir });
      }
    }

    if (removeLogs) {
      const logsDir = path.join(
        DEFAULT_PATHS.LOGS_DIR,
        "run_evaluation",
        runId,
      );
      try {
        await fs.rm(logsDir, { recursive: true, force: true });
        logger.info("Removed logs directory", { logsDir });
      } catch (error) {
        logger.warn("Could not remove logs directory", { error, logsDir });
      }
    }
  } catch (error) {
    logger.error("Error during cleanup", { error, runId });
  }
}

/**
 * Validates the environment for SWE-bench evaluation
 */
export async function validateEnvironment(): Promise<{
  valid: boolean;
  errors: string[];
}> {
  const errors: string[] = [];

  // Check Docker
  const dockerAvailable = await checkDockerAvailable();
  if (!dockerAvailable) {
    errors.push(
      "Docker is not installed or not running. Please install Docker and ensure it's running.",
    );
  }

  // Check SWE-bench Python package
  const swebenchInstalled = await checkSWEBenchInstalled();
  if (!swebenchInstalled) {
    errors.push(
      "SWE-bench Python package is not installed. Please run: pip install swebench",
    );
  }

  // Check Python version (should be 3.8+)
  const pythonVersion = await checkPythonVersion();
  if (!pythonVersion.valid) {
    errors.push(pythonVersion.error || "Python 3.8+ is required");
  }

  return {
    valid: errors.length === 0,
    errors,
  };
}

/**
 * Checks Python version
 */
async function checkPythonVersion(): Promise<{
  valid: boolean;
  error?: string;
}> {
  return new Promise((resolve) => {
    const pythonCheck = spawn("python", ["--version"]);
    let output = "";

    pythonCheck.stdout.on("data", (data: Buffer) => {
      output += data.toString();
    });

    pythonCheck.stderr.on("data", (data: Buffer) => {
      output += data.toString();
    });

    pythonCheck.on("close", (code: number | null) => {
      if (code === 0) {
        // Parse version from output like "Python 3.9.7"
        const match = output.match(/Python (\d+)\.(\d+)/);
        if (match) {
          const major = parseInt(match[1]);
          const minor = parseInt(match[2]);
          if (major === 3 && minor >= 8) {
            resolve({ valid: true });
          } else {
            resolve({
              valid: false,
              error: `Python 3.8+ required, found ${major}.${minor}`,
            });
          }
        } else {
          resolve({ valid: false, error: "Could not parse Python version" });
        }
      } else {
        resolve({ valid: false, error: "Python not found" });
      }
    });

    pythonCheck.on("error", () => {
      resolve({ valid: false, error: "Python not found" });
    });
  });
}
