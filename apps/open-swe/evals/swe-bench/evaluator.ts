import { SimpleEvaluationResult } from "langsmith/vitest";
import { createLogger, LogLevel } from "../../src/utils/logger.js";
import { TargetRepository } from "@open-swe/shared/open-swe/types";
import {
  SWEBenchInput,
  SWEBenchPrediction,
  SWEBenchHarnessOptions,
} from "./types.js";
import { generatePatchFromBranch } from "./patch-generator.js";
import {
  writePredictionsToFile,
  runSWEBenchHarness,
  loadEvaluationReport,
  validateEnvironment,
  cleanupEvaluation,
} from "./harness.js";
import * as path from "path";
import { v4 as uuidv4 } from "uuid";

const logger = createLogger(LogLevel.INFO, "SWEBenchEvaluator");

/**
 * Input structure for the SWE-bench evaluator
 */
export interface SWEBenchEvaluatorInput {
  /**
   * The SWE-bench dataset input
   */
  sweBenchInput: SWEBenchInput;

  /**
   * The agent's output containing branch and repository information
   */
  agentOutput: {
    branchName: string;
    targetRepository: TargetRepository;
  };

  /**
   * Optional GitHub token for authentication
   */
  githubToken?: string;

  /**
   * Optional model name to use in predictions
   * Default: "open-swe-agent"
   */
  modelName?: string;

  /**
   * Optional harness configuration overrides
   */
  harnessOptions?: Partial<SWEBenchHarnessOptions>;

  /**
   * Whether to cleanup artifacts after evaluation
   * Default: true
   */
  cleanup?: boolean;

  /**
   * Base directory for storing predictions and results
   * Default: "./swe-bench-evals"
   */
  baseDir?: string;
}

/**
 * Main SWE-bench evaluator function
 * Integrates with the existing evaluation pipeline and returns SimpleEvaluationResult
 */
export async function sweBenchEvaluator(
  input: SWEBenchEvaluatorInput,
): Promise<SimpleEvaluationResult[]> {
  const {
    sweBenchInput,
    agentOutput,
    githubToken,
    modelName = "open-swe-agent",
    harnessOptions = {},
    cleanup = true,
    baseDir = "./swe-bench-evals",
  } = input;

  const results: SimpleEvaluationResult[] = [];
  const runId = `swe-bench-${Date.now()}-${uuidv4().slice(0, 8)}`;

  try {
    // Validate environment first
    logger.info("Validating environment for SWE-bench evaluation...");
    const envValidation = await validateEnvironment();
    if (!envValidation.valid) {
      logger.error("Environment validation failed", {
        errors: envValidation.errors,
      });
      results.push({
        key: "environment_validation",
        score: 0,
        comment: `Environment validation failed: ${envValidation.errors.join("; ")}`,
      });
      return results;
    }

    // Generate patch from the agent's branch
    logger.info("Generating patch from agent branch...", {
      branch: agentOutput.branchName,
      repo: `${agentOutput.targetRepository.owner}/${agentOutput.targetRepository.repo}`,
    });

    const patchResult = await generatePatchFromBranch({
      branchName: agentOutput.branchName,
      targetRepository: agentOutput.targetRepository,
      githubToken,
      excludeTests: true, // SWE-bench wants non-test changes only
    });

    if (!patchResult.patch) {
      logger.warn("No patch generated from branch", {
        error: patchResult.error,
        branch: agentOutput.branchName,
      });
      results.push({
        key: "patch_generation",
        score: 0,
        comment: patchResult.error || "No changes found in branch",
      });
      return results;
    }

    logger.info("Patch generated successfully", {
      modifiedFiles: patchResult.modifiedFiles.length,
      patchSize: patchResult.patch.length,
    });

    // Create prediction for SWE-bench
    const prediction: SWEBenchPrediction = {
      instance_id: sweBenchInput.instance_id,
      model_name_or_path: modelName,
      model_patch: patchResult.patch,
    };

    // Write prediction to JSONL file
    const predictionsDir = path.join(baseDir, "predictions");
    const predictionsPath = path.join(predictionsDir, `${runId}.jsonl`);

    logger.info("Writing prediction to file...", { predictionsPath });
    await writePredictionsToFile([prediction], predictionsPath);

    // Prepare harness options
    const fullHarnessOptions: SWEBenchHarnessOptions = {
      dataset_name:
        harnessOptions.dataset_name || "princeton-nlp/SWE-bench_Lite",
      predictions_path: predictionsPath,
      run_id: runId,
      max_workers: harnessOptions.max_workers || 1, // Single instance evaluation
      cache_level: harnessOptions.cache_level || "env",
      timeout: harnessOptions.timeout || 1800,
      instance_ids: sweBenchInput.instance_id, // Evaluate only this instance
      clean: harnessOptions.clean || false,
      split: harnessOptions.split || "test",
      force_rebuild: harnessOptions.force_rebuild || false,
      open_file_limit: harnessOptions.open_file_limit || 4096,
    };

    // Run SWE-bench harness
    logger.info("Running SWE-bench evaluation harness...", {
      instance_id: sweBenchInput.instance_id,
      run_id: runId,
    });

    const harnessResult = await runSWEBenchHarness(fullHarnessOptions);

    if (!harnessResult.success) {
      logger.error("SWE-bench harness failed", {
        error: harnessResult.error,
        output: harnessResult.output.slice(-1000), // Last 1000 chars
      });
      results.push({
        key: "harness_execution",
        score: 0,
        comment: `Harness failed: ${harnessResult.error}`,
      });
      return results;
    }

    // Load and parse evaluation results
    logger.info("Loading evaluation results...");
    const report = await loadEvaluationReport(
      runId,
      modelName,
      fullHarnessOptions.dataset_name,
      fullHarnessOptions,
    );

    if (!report) {
      logger.error("Failed to load evaluation report");
      results.push({
        key: "results_parsing",
        score: 0,
        comment: "Failed to load evaluation results",
      });
      return results;
    }

    // Convert SWE-bench results to SimpleEvaluationResult format
    const instanceResult = report.instance_results.find(
      (r) => r.instance_id === sweBenchInput.instance_id,
    );

    if (!instanceResult) {
      logger.error("Instance result not found in report", {
        instance_id: sweBenchInput.instance_id,
      });
      results.push({
        key: "instance_result",
        score: 0,
        comment: "Instance result not found in evaluation report",
      });
      return results;
    }

    // Primary result: whether the instance was resolved
    results.push({
      key: "resolved",
      score: instanceResult.resolved ? 1 : 0,
      comment: instanceResult.resolved
        ? "Successfully resolved the issue"
        : "Failed to resolve the issue",
    });

    // Patch application result
    results.push({
      key: "patch_applied",
      score: instanceResult.patch_successfully_applied ? 1 : 0,
      comment: instanceResult.patch_successfully_applied
        ? "Patch applied successfully"
        : "Failed to apply patch",
    });

    // Test metrics
    if (
      instanceResult.fail_to_pass.length > 0 ||
      sweBenchInput.FAIL_TO_PASS?.length
    ) {
      const expectedFailToPass = sweBenchInput.FAIL_TO_PASS?.length || 0;
      const actualFailToPass = instanceResult.fail_to_pass.length;
      const failToPassScore =
        expectedFailToPass > 0
          ? actualFailToPass / expectedFailToPass
          : actualFailToPass > 0
            ? 1
            : 0;

      results.push({
        key: "fail_to_pass",
        score: failToPassScore,
        comment: `Fixed ${actualFailToPass} out of ${expectedFailToPass} failing tests`,
      });
    }

    if (
      instanceResult.pass_to_pass.length > 0 ||
      sweBenchInput.PASS_TO_PASS?.length
    ) {
      const expectedPassToPass = sweBenchInput.PASS_TO_PASS?.length || 0;
      const actualPassToPass = instanceResult.pass_to_pass.length;
      const passToPassScore =
        expectedPassToPass > 0 ? actualPassToPass / expectedPassToPass : 1; // If no expected, score 1 if all actual pass

      results.push({
        key: "pass_to_pass",
        score: passToPassScore,
        comment: `Maintained ${actualPassToPass} out of ${expectedPassToPass} passing tests`,
      });
    }

    // Check for test regressions
    if (instanceResult.pass_to_fail && instanceResult.pass_to_fail.length > 0) {
      results.push({
        key: "test_regressions",
        score: 0,
        comment: `Introduced ${instanceResult.pass_to_fail.length} test regressions`,
      });
    }

    // Add metadata results
    results.push({
      key: "modified_files",
      score: patchResult.modifiedFiles.length,
      comment: `Modified ${patchResult.modifiedFiles.length} files: ${patchResult.modifiedFiles.join(", ")}`,
    });

    if (instanceResult.duration) {
      results.push({
        key: "evaluation_duration",
        score: instanceResult.duration,
        comment: `Evaluation took ${instanceResult.duration} seconds`,
      });
    }

    // Log summary
    logger.info("SWE-bench evaluation completed", {
      instance_id: sweBenchInput.instance_id,
      resolved: instanceResult.resolved,
      fail_to_pass: instanceResult.fail_to_pass.length,
      pass_to_pass: instanceResult.pass_to_pass.length,
      pass_to_fail: instanceResult.pass_to_fail?.length || 0,
    });
  } catch (error) {
    logger.error("SWE-bench evaluation failed with error", { error });
    results.push({
      key: "evaluation_error",
      score: 0,
      comment: `Evaluation error: ${error instanceof Error ? error.message : String(error)}`,
    });
  } finally {
    // Cleanup if requested
    if (cleanup) {
      logger.info("Cleaning up evaluation artifacts...");
      await cleanupEvaluation(runId, {
        removePredictions: true,
        removeResults: true,
        removeLogs: true,
      });
    }
  }

  return results;
}

/**
 * Batch evaluator for multiple SWE-bench instances
 */
export async function sweBenchBatchEvaluator(
  inputs: SWEBenchEvaluatorInput[],
  options: {
    maxConcurrency?: number;
    aggregateResults?: boolean;
  } = {},
): Promise<SimpleEvaluationResult[]> {
  const { maxConcurrency = 1, aggregateResults = true } = options;

  const allResults: SimpleEvaluationResult[] = [];

  // Process in batches to respect maxConcurrency
  for (let i = 0; i < inputs.length; i += maxConcurrency) {
    const batch = inputs.slice(i, i + maxConcurrency);
    const batchPromises = batch.map((input) => sweBenchEvaluator(input));

    try {
      const batchResults = await Promise.all(batchPromises);
      allResults.push(...batchResults.flat());
    } catch (error) {
      logger.error("Batch evaluation failed", { error, batchIndex: i });
    }
  }

  if (aggregateResults) {
    // Add aggregate metrics
    const totalInstances = inputs.length;
    const resolvedInstances = allResults.filter(
      (r) => r.key === "resolved" && r.score === 1,
    ).length;

    allResults.push({
      key: "aggregate_resolution_rate",
      score: totalInstances > 0 ? resolvedInstances / totalInstances : 0,
      comment: `Resolved ${resolvedInstances} out of ${totalInstances} instances`,
    });
  }

  return allResults;
}

/**
 * Helper function to create SWE-bench input from common formats
 */
export function createSWEBenchInput(
  instanceId: string,
  repo: string,
  problemStatement: string,
  additionalFields?: Partial<SWEBenchInput>,
): SWEBenchInput {
  return {
    instance_id: instanceId,
    repo,
    problem_statement: problemStatement,
    ...additionalFields,
  };
}

/**
 * Helper to extract instance ID from repo and issue number
 */
export function formatInstanceId(
  owner: string,
  repo: string,
  issueNumber: number,
): string {
  return `${owner}__${repo}-${issueNumber}`;
}
