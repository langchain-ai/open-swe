import "@langchain/langgraph/zod";
import { createDeepAgent } from "deepagents";
import "dotenv/config";
import { codeReviewerAgent, testGeneratorAgent } from "./subagents.js";
import { getCodingInstructions } from "./prompts.js";
import { createAgentPostModelHook } from "./post-model-hook.js";
import { CodingAgentState } from "./state.js";
import { executeBash, httpRequest, webSearch } from "./tools.js";

const codingInstructions = getCodingInstructions();
const postModelHook = createAgentPostModelHook();

const agent = createDeepAgent({
  tools: [executeBash, httpRequest, webSearch],
  instructions: codingInstructions,
  subagents: [codeReviewerAgent, testGeneratorAgent],
  isLocalFileSystem: true,
  postModelHook: postModelHook,
  stateSchema: CodingAgentState,
}).withConfig({ recursionLimit: 1000 }) as any;

export { agent, executeBash, httpRequest, webSearch };
