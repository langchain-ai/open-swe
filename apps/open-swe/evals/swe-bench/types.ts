/**
 * SWE-bench evaluation types
 */

/**
 * Input structure for SWE-bench evaluations
 * Represents a single instance from the SWE-bench dataset
 */
export interface SWEBenchInput {
  /**
   * Unique identifier for the SWE-bench instance
   * Format: "repo_owner__repo_name-issue_number"
   * Example: "sympy__sympy-20590"
   */
  instance_id: string;

  /**
   * Repository information in "owner/repo" format
   * Example: "sympy/sympy"
   */
  repo: string;

  /**
   * The problem statement or issue description that needs to be solved
   * This is what gets passed to the agent to fix
   */
  problem_statement: string;

  /**
   * Optional: Base commit SHA where the issue exists
   */
  base_commit?: string;

  /**
   * Optional: Version of the repository
   */
  version?: string;

  /**
   * Optional: Test patch that verifies the fix
   */
  test_patch?: string;

  /**
   * Optional: Expected test results (fail_to_pass, pass_to_pass)
   */
  FAIL_TO_PASS?: string[];
  PASS_TO_PASS?: string[];
}

/**
 * Prediction format for SWE-bench evaluation
 * This is what gets written to the JSONL predictions file
 */
export interface SWEBenchPrediction {
  /**
   * Instance ID matching the SWEBenchInput
   * Must match exactly with the dataset instance_id
   */
  instance_id: string;

  /**
   * Model or agent name/identifier
   * Example: "open-swe-agent", "gpt-4", etc.
   */
  model_name_or_path: string;

  /**
   * The patch predicted by the model as a unified diff string
   * Can be null if no patch is generated
   * Format: Standard git diff output
   */
  model_patch: string | null;
}

/**
 * Result structure from SWE-bench evaluation
 */
export interface SWEBenchResult {
  /**
   * Instance ID for this result
   */
  instance_id: string;

  /**
   * Whether the instance was resolved (all fail_to_pass tests now pass)
   */
  resolved: boolean;

  /**
   * Tests that were failing and now pass after applying the patch
   */
  fail_to_pass: string[];

  /**
   * Tests that were passing and still pass after applying the patch
   */
  pass_to_pass: string[];

  /**
   * Tests that were passing but now fail after applying the patch
   */
  pass_to_fail?: string[];

  /**
   * Tests that were failing and still fail after applying the patch
   */
  fail_to_fail?: string[];

  /**
   * Whether the patch was successfully applied
   */
  patch_successfully_applied: boolean;

  /**
   * Error message if evaluation failed
   */
  error?: string;

  /**
   * Duration of the evaluation in seconds
   */
  duration?: number;
}

/**
 * Options for running the SWE-bench harness
 */
export interface SWEBenchHarnessOptions {
  /**
   * Name of the dataset to evaluate against
   * Example: "princeton-nlp/SWE-bench_Lite" or "princeton-nlp/SWE-bench"
   */
  dataset_name: string;

  /**
   * Path to the predictions JSONL file
   */
  predictions_path: string;

  /**
   * Maximum number of parallel workers for evaluation
   * Default: 8
   */
  max_workers?: number;

  /**
   * Unique identifier for this evaluation run
   * Used for organizing logs and results
   */
  run_id: string;

  /**
   * Docker image cache level
   * Options: "none", "base", "env", "instance"
   * Default: "env"
   */
  cache_level?: "none" | "base" | "env" | "instance";

  /**
   * Timeout for each instance evaluation in seconds
   * Default: 1800 (30 minutes)
   */
  timeout?: number;

  /**
   * Specific instance IDs to evaluate (comma-separated)
   * If not provided, evaluates all instances in predictions file
   */
  instance_ids?: string;

  /**
   * Whether to clean up Docker resources after evaluation
   * Default: false
   */
  clean?: boolean;

  /**
   * Split of the dataset to use
   * Default: "test"
   */
  split?: string;

  /**
   * Whether to force rebuild Docker images
   * Default: false
   */
  force_rebuild?: boolean;

  /**
   * Open file limit for the process
   * Default: 4096
   */
  open_file_limit?: number;
}

/**
 * Aggregate evaluation metrics
 */
export interface SWEBenchMetrics {
  /**
   * Total number of instances in the dataset
   */
  total_instances: number;

  /**
   * Number of instances the model attempted (had predictions for)
   */
  instances_submitted: number;

  /**
   * Number of instances that completed evaluation
   */
  instances_completed: number;

  /**
   * Number of instances successfully resolved
   */
  instances_resolved: number;

  /**
   * Resolution rate (resolved / submitted)
   */
  resolution_rate: number;

  /**
   * Average fail_to_pass rate across all instances
   */
  avg_fail_to_pass: number;

  /**
   * Average pass_to_pass rate across all instances
   */
  avg_pass_to_pass: number;

  /**
   * Instances that had errors during evaluation
   */
  instances_with_errors: number;

  /**
   * Instances where patch failed to apply
   */
  instances_patch_failed: number;
}

/**
 * Full evaluation report combining results and metrics
 */
export interface SWEBenchEvaluationReport {
  /**
   * Run ID for this evaluation
   */
  run_id: string;

  /**
   * Model name/identifier
   */
  model: string;

  /**
   * Dataset name
   */
  dataset: string;

  /**
   * Timestamp when evaluation started
   */
  started_at: string;

  /**
   * Timestamp when evaluation completed
   */
  completed_at: string;

  /**
   * Aggregate metrics
   */
  metrics: SWEBenchMetrics;

  /**
   * Individual instance results
   */
  instance_results: SWEBenchResult[];

  /**
   * Evaluation configuration used
   */
  config: SWEBenchHarnessOptions;
}
