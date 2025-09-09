/**
 * Creates a LangSmith run URL for tracking programmer runs
 * @param runId - The specific run ID to link to
 * @returns Complete LangSmith URL for the run
 */
export function createProgrammerRunURL(runId: string): string {
  const workspaceId = process.env.LANGSMITH_WORKSPACE_ID;
  const projectId = process.env.LANGSMITH_PROJECT_ID;

  if (!workspaceId || !projectId) {
    throw new Error(
      "LANGSMITH_WORKSPACE_ID and LANGSMITH_PROJECT_ID environment variables are required",
    );
  }

  return `https://smith.langchain.com/o/${workspaceId}/projects/p/${projectId}/r/${runId}`;
}
