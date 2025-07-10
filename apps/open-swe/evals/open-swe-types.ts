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
