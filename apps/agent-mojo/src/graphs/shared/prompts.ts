export const GITHUB_WORKFLOWS_PERMISSIONS_PROMPT = `
IMPORTANT: You do not have permissions to EDIT or DELETE files inside the GitHub workflows directory (commonly found at .github/workflows/).
  - If you need to modify or create a workflow, ensure you always do so inside a 'tmp-workflows' directory.
  - Any attempt to create or modify a workflow file in the .github/workflows/ directory will result in a fatal error that will end the session.
  - Notify the user that they will need to manually move the workflow file from the 'tmp-workflows' directory to the .github/workflows/ directory since you do not have permissions to do so.
`;
