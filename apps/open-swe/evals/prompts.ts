import { OpenSWEInput } from "./open-swe-types.js";
import { TargetRepository } from "@openswe/shared/open-swe/types";
import { HumanMessage } from "@langchain/core/messages";
import { Octokit } from "@octokit/rest";
import { ManagerGraphUpdate } from "@openswe/shared/open-swe/manager/types";

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

  const PROMPT_TEMPLATE = `<request>
{USER_REQUEST}
</request>
<codebase-readme>
{CODEBASE_README}
</codebase-readme>

CRITICAL: Your code will be evaluated by an automated LangGraph evaluation script. Follow these requirements EXACTLY or your submission will receive a score of 0.0:

## REQUIRED FILE STRUCTURE
1. **agent.py** - Must be at the project root
2. **langgraph.json** - Must be at the project root

## AGENT.PY REQUIREMENTS
Your agent.py file MUST:

1. **Export the compiled graph correctly**:
   \`\`\`python
   # The evaluator looks for these attributes in order of preference:
   app = your_compiled_graph  # PREFERRED - consistent with other prompts
   # OR (fallback options)
   compiled_graph = your_compiled_graph  
   # OR
   graph = your_compiled_graph
   \`\`\`

2. **Use the exact State schema format**:
   \`\`\`python
   from typing import TypedDict
   from langchain_core.messages import HumanMessage

   class State(TypedDict):
       messages: list  # REQUIRED - evaluator sends input here
       # ... add your other fields as needed
   \`\`\`

3. **Handle the evaluation input format**:
   The evaluator will ALWAYS call your graph with ONLY:
   \`\`\`python
   {"messages": [HumanMessage(content="actual_user_input")]}
   \`\`\`
   
   **IMPORTANT**: The evaluator provides ONLY user input - nothing else. Your code must:
   - Extract user input from \`state["messages"][0].content\`
   - Provide default values for ANY other state fields you need
   - Never assume other state fields will be provided by the evaluator

   \`\`\`python
   def your_first_node(state: State):
       # Extract user input (the ONLY thing evaluator provides)
       user_input = state["messages"][0].content if state["messages"] else ""
       
       # Initialize any other state fields with defaults if needed
       current_step = state.get("current_step", 0)  # Default to 0
       user_data = state.get("user_data", {})       # Default to empty dict
       
       # Your processing logic here...
       
       return {
           "messages": state["messages"] + [AIMessage(content=response)],
           "current_step": current_step + 1,  # Update as needed
           "user_data": updated_data          # Update as needed
       }
   \`\`\`

4. **Complete working example structure**:
   \`\`\`python
   from typing import Annotated
   from typing_extensions import TypedDict
   from langgraph.graph import StateGraph, START, END
   from langgraph.graph.message import add_messages
   from langchain_core.messages import HumanMessage, AIMessage

   class State(TypedDict):
       # REQUIRED: messages field with add_messages reducer
       messages: Annotated[list, add_messages]
       # Add other fields as needed with default handling:
       # current_step: int  # Will default to missing, handle with .get()
       # user_data: dict    # Will default to missing, handle with .get()

   def process_input(state: State):
       # Extract user input (ONLY thing evaluator provides)
       user_input = state["messages"][0].content if state["messages"] else ""
       
       # Handle other state fields with defaults
       step = state.get("current_step", 0)  # Default to 0
       data = state.get("user_data", {})    # Default to empty dict
       
       # Your logic here
       response = f"Processed: {user_input} (Step: {step})"
       
       # Return updates - add_messages will append the AI message
       return {
           "messages": [AIMessage(content=response)],
           "current_step": step + 1,
           "user_data": data  # or updated data
       }

   # Build graph
   graph_builder = StateGraph(State)
   graph_builder.add_node("process", process_input)
   graph_builder.add_edge(START, "process")
   graph_builder.add_edge("process", END)

   # REQUIRED: Export compiled graph (use 'app' for consistency)
   app = graph_builder.compile()
   \`\`\`

## LANGGRAPH.JSON REQUIREMENTS
Create this exact file at project root:
\`\`\`json
{
    "dependencies": ["."],
    "graphs": {
        "agent": "./agent.py:app"
    },
    "env": ".env"
}
\`\`\`

## CRITICAL RESTRICTIONS
- **Evaluator provides ONLY user input** - The evaluator will ONLY provide \`{"messages": [HumanMessage(content="user_input")]}\`. Your code must handle all other state fields with default values using \`.get()\` or similar patterns.
- **NO print() statements** - They interfere with evaluation scoring
- **NO logging.info/debug/etc** - All logging is disabled during evaluation  
- **NO sys.stdout writes** - Output is captured and affects scoring
- **Handle import errors gracefully** - Wrap imports in try/catch if needed
- **Test your graph locally** - Ensure \`compiled_graph.invoke({"messages": [HumanMessage(content="test")]})\` works

## EVALUATION PROCESS
The evaluator will:
1. Import your module: \`importlib.import_module("agent")\`
2. Look for \`app\`, \`compiled_graph\`, or \`graph\` attribute
3. Call: \`your_graph.invoke({"messages": [HumanMessage(content="user_question")]})\`  
4. Evaluate the output against criteria (relevance, completeness, accuracy, clarity)
5. The evaluator will install the dependencies from requirements.txt in your branch. Regardless of whether there's an existing requirements.txt from the branch you checked out,
you always need a complete requirements.txt file in your branch.

**CRITICAL**: The evaluator provides ONLY the user's question in messages. Any other state fields your graph needs must have default values handled in your code.

## OUTPUT FORMAT EXPECTATIONS
Your graph should return a State dict with:
- \`messages\` list containing the conversation
- Any other state fields you defined
- The final AI response should be in the last message


## FAILURE MODES TO AVOID
- Missing agent.py file → Score: 0.0
- Missing app/compiled_graph/graph export → Score: 0.0  
- Import errors in agent.py → Score: 0.0
- Graph doesn't handle the messages format → Score: 0.0
- Print statements corrupting output → Reduced score

Generate clean, working code that follows these requirements exactly.`;

  const userMessageContent = PROMPT_TEMPLATE.replace("{REPO}", inputs.repo)
    .replace("{USER_REQUEST}", inputs.user_input)
    .replace("{CODEBASE_README}", readmeContents);

  const userMessage = new HumanMessage(userMessageContent);
  return {
    messages: [userMessage],
    targetRepository,
    autoAcceptPlan: true,
  };
}
