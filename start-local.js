import { exec } from "child_process";
import fs from "fs";
import path from "path";

// The interactive prompt is disabled because it's not supported in the test environment.
// To use the interactive prompt, comment out the following code and uncomment the
// inquirer code below.

const repoPath = "./repos";
const ollamaApiUrl = "http://localhost:11434";

const envContent = `
# ------------------LLM Provider Keys------------------
# The URL of the local LLM endpoint. Defaults to Ollama.
OLLAMA_API_URL="${ollamaApiUrl}"
# The name of the model to use. Should be a 7B/8B model for local use.
OLLAMA_MODEL="llama3"


# ------------------Infrastructure---------------------
# The path to the local repository to run the agent on.
LOCAL_REPO_PATH="${repoPath}"


# ------------------------Other------------------------
# Defaults to 2024 if not set.
PORT="2024"
# Whether or not to append the string "[skip ci]" to the commit message.
SKIP_CI_UNTIL_LAST_COMMIT="true"
`;

const envPath = path.join("apps", "open-swe", ".env");

fs.writeFileSync(envPath, envContent.trim());

console.log(".env file created successfully!");

console.log("Starting the Open SWE CLI...");

const cliProcess = exec("yarn workspace @open-swe/cli dev");

cliProcess.stdout.on("data", (data) => {
  console.log(data);
});

cliProcess.stderr.on("data", (data) => {
  console.error(data);
});

cliProcess.on("close", (code) => {
  console.log(`CLI process exited with code ${code}`);
});
