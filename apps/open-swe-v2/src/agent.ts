import "@langchain/langgraph/zod";
import { createDeepAgent } from "deepagents";
import "dotenv/config";
import { code_reviewer_agent, test_generator_agent } from "./subagents.js";
import { getCodingInstructions } from "./prompts.js";
import { createCodingAgentPostModelHook } from "./post-model-hook.js";
import { CodingAgentState } from "./state.js";
import { executeBash, httpRequest, webSearch } from "./tools.js";

if (process.env.LANGCHAIN_TRACING_V2 !== "false") {
  process.env.LANGCHAIN_TRACING_V2 = "true";
  if (!process.env.LANGCHAIN_PROJECT) {
    process.env.LANGCHAIN_PROJECT = "coding_agent";
  }
}

const codingInstructions = getCodingInstructions();
const postModelHook = createCodingAgentPostModelHook();

const agent = createDeepAgent({
  tools: [executeBash, httpRequest, webSearch],
  instructions: codingInstructions,
  subagents: [code_reviewer_agent, test_generator_agent],
  isLocalFileSystem: true,
  postModelHook: postModelHook,
  stateSchema: CodingAgentState,
}).withConfig({ recursionLimit: 1000 }) as any;

export { agent, executeBash, httpRequest, webSearch };
