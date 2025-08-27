/* eslint-disable no-console */
import { createDeepAgent } from "deepagents";
import { applyConfigToEnv } from "@open-swe/shared";
import "dotenv/config";

// Apply saved configuration to environment variables
applyConfigToEnv();
import { code_reviewer_agent, test_generator_agent } from "./subagents.js";
import { get_coding_instructions } from "./prompts.js";
import { createCodingAgentPostModelHook } from "./post-model-hook.js";
import { CodingAgentState } from "./state.js";
import {
  executeBash,
  httpRequest,
  webSearch,
  ls,
  readFile,
  writeFile,
  strReplaceBasedEditTool,
  grep,
  writeTodos,
  glob,
} from "./tools.js";

// LangSmith tracing setup
if (process.env.LANGCHAIN_TRACING_V2 !== "false") {
  process.env.LANGCHAIN_TRACING_V2 = "true";
  if (!process.env.LANGCHAIN_PROJECT) {
    process.env.LANGCHAIN_PROJECT = "coding_agent";
  }
}

const codingInstructions = get_coding_instructions();
const postModelHook = createCodingAgentPostModelHook();
// Create the coding agent
const tools = [
  executeBash,
  httpRequest,
  webSearch,
  ls,
  readFile,
  writeFile,
  strReplaceBasedEditTool,
  grep,
  glob,
  writeTodos,
];

const agent = createDeepAgent({
  tools,
  instructions: codingInstructions,
  subagents: [code_reviewer_agent, test_generator_agent],
  skipBuiltinTools: true,
  postModelHook: postModelHook,
  stateSchema: CodingAgentState,
}).withConfig({ recursionLimit: 50 }) as any; // reduced to prevent token overflow

export {
  agent,
  executeBash,
  httpRequest,
  webSearch,
  ls,
  glob,
  readFile,
  writeFile,
  strReplaceBasedEditTool,
  grep,
  writeTodos,
};
