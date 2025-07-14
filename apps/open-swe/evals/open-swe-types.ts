/**
 * Input structure for Open SWE evaluations
 * This is much simpler than SWE-Bench since we only need
 * problem statement + repo info for ruff/mypy analysis
 */
export interface OpenSWEInput {
  /**
   * The user request/problem statement that was given to Open SWE
   * This is what gets passed to the agent to solve
   */
  user_input: string;

  /**
   * Repository information in "owner/repo" format
   * e.g., "aliyanishfaq/my-project"
   */
  repo: string;

  /**
   * Optional: Branch name where the agent's solution is located
   * If not provided, agent will create one (e.g., "open-swe/uuid")
   */
  branch: string;
}

/**
 * Process execution options
 */
export interface ExecOptions {
  command: string;
  workingDir: string;
  env: Record<string, string> | undefined;
  timeoutSec: number;
}

/**
 * Ruff issue location
 */
export interface RuffLocation {
  column: number;
  row: number;
}

/**
 * Ruff fix edit
 */
export interface RuffEdit {
  content: string;
  end_location: RuffLocation;
  location: RuffLocation;
}

/**
 * Ruff fix suggestion
 */
export interface RuffFix {
  applicability: "safe" | "unsafe" | "display";
  edits: RuffEdit[];
  message: string;
}

/**
 * Individual Ruff issue
 */
export interface RuffIssue {
  cell: string | null;
  code: string;
  end_location: RuffLocation;
  filename: string;
  fix: RuffFix | null;
  location: RuffLocation;
  message: string;
  noqa_row: number;
  url: string;
}

/**
 * Return type for ruffPromise function
 */
export interface RuffResult {
  ruffScore: number;
  error: Error | null;
  issues: RuffIssue[];
}

/**
 * Return type for mypyPromise function
 */
export interface MyPyResult {
  mypyScore: number;
  error: Error | null;
  issues: string[];
}

export interface CodeTestDetails {
  ruff: {
    issues: RuffIssue[];
    error: Error | null;
  };
  mypy: {
    issues: string[];
    error: Error | null;
  };
}
