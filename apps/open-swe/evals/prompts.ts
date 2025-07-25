import { OpenSWEInput } from "./open-swe-types.js";
import { TargetRepository } from "@open-swe/shared/open-swe/types";
import { HumanMessage } from "@langchain/core/messages";
import { Octokit } from "@octokit/rest";
import { ManagerGraphUpdate } from "@open-swe/shared/open-swe/manager/types";

async function getRepoReadmeContents(
  targetRepository: TargetRepository,
): Promise<string> {
  if (!process.env.GITHUB_PAT) {
    throw new Error("GITHUB_PAT environment variable missing.");
  }
  const octokit = new Octokit({
    auth: process.env.GITHUB_PAT,
  });

  try {
    const { data } = await octokit.repos.getReadme({
      owner: targetRepository.owner,
      repo: targetRepository.repo,
    });
    return Buffer.from(data.content, "base64").toString("utf-8");
  } catch (_) {
    return "";
  }
}

export async function formatInputs(
  inputs: OpenSWEInput,
): Promise<ManagerGraphUpdate> {
  const targetRepository: TargetRepository = {
    owner: inputs.repo.split("/")[0],
    repo: inputs.repo.split("/")[1],
    branch: inputs.branch,
  };

  const readmeContents = await getRepoReadmeContents(targetRepository);

  const SIMPLE_PROMPT_TEMPLATE = `<request>
{USER_REQUEST}
</request>

<codebase-readme>
{CODEBASE_README}
</codebase-readme>

IMPORTANT: We'll run a LangGraph evaluation script on your code. Make sure to:

1. Create a file at the project root named 'agent.py'.
2. In 'agent.py', import and build your graph, e.g.:

     from langgraph.graph import StateGraph, START, END
     # … define nodes …
     graph = graph_builder.compile()

3. Export the compiled graph as:

     compiled_graph = graph

4. **EVALUATION INPUT FORMAT**: The evaluation script will always provide input as:
     {"messages": [HumanMessage(content="user_input_here")]}
   
   Your State schema MUST include:
     class State(TypedDict):
         messages: list  # Required for evaluation compatibility
         # ... your other fields
   
   Extract the actual input in your first node:
     if "messages" in state and state["messages"]:
         user_input = state["messages"][0].content
     else:
         user_input = state.get("your_field", "")

5. Create a 'langgraph.json' file at the project root:
     {
       "dependencies": ["."],
       "graphs": {
         "agent": "./agent.py:compiled_graph"
       },
       "env": ".env"
     }

If agent.py is missing, doesn't export compiled_graph, or langgraph.json is missing, your submission will score 0.
Don't add logger or unnecessary print statements as they will be captured by the evaluation script,
interfering with the evaluation.
`;

  const userMessageContent = SIMPLE_PROMPT_TEMPLATE.replace(
    "{REPO}",
    inputs.repo,
  )
    .replace("{USER_REQUEST}", inputs.user_input)
    .replace("{CODEBASE_README}", readmeContents);

  const userMessage = new HumanMessage(userMessageContent);
  return {
    messages: [userMessage],
    targetRepository,
    autoAcceptPlan: true,
  };
}
