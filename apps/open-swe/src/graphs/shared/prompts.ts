import { createDevServerToolFields } from "@open-swe/shared/open-swe/tools";

export const GITHUB_WORKFLOWS_PERMISSIONS_PROMPT = `
IMPORTANT: You do not have permissions to EDIT or DELETE files inside the GitHub workflows directory (commonly found at .github/workflows/).
  - If you need to modify or create a workflow, ensure you always do so inside a 'tmp-workflows' directory.
  - Any attempt to create or modify a workflow file in the .github/workflows/ directory will result in a fatal error that will end the session.
  - Notify the user that they will need to manually move the workflow file from the 'tmp-workflows' directory to the .github/workflows/ directory since you do not have permissions to do so.
`;

const dummyDevServerToolFields = createDevServerToolFields({
  owner: "dummy",
  repo: "dummy",
});
export const DEV_SERVER_USAGE_PROMPT = `### Dev server tool
The \`${dummyDevServerToolFields.name}\` tool allows Claude to start a server locally, and execute commands against them (e.g. a curl request, or a shell command which makes a request to the server).

This tool should be used to test changes made to a web application (start web server & verify it's running), an API server (start server, write tests which make requests to the server, and execute those requests), or other service which can be run locally.

IMPORTANT: If making changes, or building new features which are on a web, API server, or other service it is important to attempt to test your changes with this tool. Never blindly assume your code works. Remember what they say: "Trust, but verify". You should follow the same principle when writing code.

IMPORTANT: You may not always have the proper permissions, secrets, or access to start & call the server. If you run into repeated errors which are out of your control, stop calling this tool and do not attempt to start the server (you want to avoid an unending loop of errors).

**Cases where you should use this tool:**
- Test that a web application starts correctly after implementing features
- Verify API endpoints respond properly after adding/modifying them  
- Debug server startup issues or runtime errors
- Validate that changes work in a running environment

Common development server commands by technology:
- **Python/LangGraph**: \`langgraph dev\` (for LangGraph applications)
- **Node.js/React**: \`npm start\`, \`npm run dev\`, \`yarn start\`, \`yarn dev\`
- **Python/Django**: \`python manage.py runserver\`
- **Python/Flask**: \`python app.py\`, \`flask run\`
- **Python/FastAPI**: \`uvicorn main:app --reload\`
- **Go**: \`go run .\`, \`go run main.go\`
- **Ruby/Rails**: \`rails server\`, \`bundle exec rails server\`

IMPORTANT: Never guess the startup script for a server. ALWAYS search the repository first for a pre-made script, or a documented method of starting the server.

Parameters:
  - \`serverStartupCommand\`: The command to start the server. Accepts a \`command\` array of strings which will be executed to start the server, and optional \`workdir\` and \`waitTime\` inputs.
  - \`requestCommand\`: The shell command to execute which will make a request to the server. Accepts a \`command\` array of strings which will be executed, and should make a request to the server. It also accepts optional \`workdir\` and \`timeout\` inputs.
  - \`curlCommand\`: A curl request to send to the server. Accepts a \`url\`, \`method\`, \`headers\`, \`body\`, \`query\`, \`timeout\`, and \`followRedirects\` inputs.

IMPORTANT: Only ONE of \`requestCommand\` or \`curlCommand\` must be provided. If both are provided, the tool will throw an error.

Tool implementation flow:
1. Start the server
2. Wait for the server to start (based on \`waitTime\` input, defaults to 5 seconds)
3. Send a request to the server (either via \`requestCommand\` or \`curlCommand\`)
4. Capture the server logs
5. Return the response from the server, and the complete server logs for debugging.
`;
