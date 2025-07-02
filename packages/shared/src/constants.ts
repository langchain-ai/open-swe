export const TIMEOUT_SEC = 60; // 1 minute
export const SANDBOX_ROOT_DIR = "/home/daytona";
export const SNAPSHOT_NAME = "daytonaio/langchain-open-swe:0.1.0";
export const PLAN_INTERRUPT_DELIMITER = ":::";
export const PLAN_INTERRUPT_ACTION_TITLE = "Approve/Edit Plan";

// Prefix the access token with `x-` so that it's included in requests to the LangGraph server.
export const GITHUB_TOKEN_COOKIE = "x-github-access-token";
export const GITHUB_INSTALLATION_TOKEN_COOKIE = "x-github-installation-token";

export const DO_NOT_RENDER_ID_PREFIX = "do-not-render-";
export const GITHUB_AUTH_STATE_COOKIE = "github_auth_state";
export const GITHUB_INSTALLATION_ID_COOKIE = "github_installation_id";
export const GITHUB_TOKEN_TYPE_COOKIE = "github_token_type";

export const LANGGRAPH_DOCUMENTATION = `# LangGraph Technical Reference

## What is LangGraph
LangGraph is a library for building stateful, multi-step workflows with LLMs. It creates applications as state machines with nodes (functions) and edges (connections) that can:
- Maintain conversation memory across interactions
- Integrate external tools and APIs
- Pause for human input and resume execution
- Handle complex multi-agent workflows

## Core Concepts
- **StateGraph**: The main workflow container
- **State**: Data structure (TypedDict) that flows between nodes
- **Nodes**: Functions that process state and return updates
- **Edges**: Connections between nodes (static or conditional)
- **Checkpointing**: Persistence that saves state after each step
- **Reducers**: Functions that define how state updates merge (e.g., \`add_messages\` appends to lists)

## Core Imports
\`\`\`python
from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import Command, interrupt
from langchain_core.tools import tool, InjectedToolCallId
from langchain_core.messages import ToolMessage
\`\`\`

## State Definition
State is a TypedDict that defines the data structure flowing through your graph. Use \`Annotated[list, add_messages]\` for message history (appends), regular types for other fields (overwrite).

\`\`\`python
# Basic state with messages
class State(TypedDict):
    messages: Annotated[list, add_messages]  # Appends to list

# Extended state with custom fields
class ExtendedState(TypedDict):
    messages: Annotated[list, add_messages]
    custom_field: str  # Overwrites value
    another_field: int
\`\`\`

## Graph Creation Pattern
Standard workflow: Define state → Create graph → Add nodes → Connect with edges → Compile

\`\`\`python
# 1. Define state
class State(TypedDict):
    messages: Annotated[list, add_messages]

# 2. Create graph builder
graph_builder = StateGraph(State)

# 3. Add nodes
def node_function(state: State):
    return {"messages": [response]}

graph_builder.add_node("node_name", node_function)

# 4. Add edges
graph_builder.add_edge(START, "node_name")
graph_builder.add_edge("node_name", END)

# 5. Compile
graph = graph_builder.compile()
# or with memory:
memory = MemorySaver()
graph = graph_builder.compile(checkpointer=memory)
\`\`\`

## Node Function Patterns
Nodes are functions that take current state and return state updates as dictionaries.

\`\`\`python
# Basic chatbot node
def chatbot(state: State):
    return {"messages": [llm.invoke(state["messages"])]}

# Node with tools
def chatbot_with_tools(state: State):
    message = llm_with_tools.invoke(state["messages"])
    return {"messages": [message]}

# Node with state updates
def custom_node(state: State):
    return {
        "messages": [new_message],
        "custom_field": "new_value"
    }

# Error handling node
def safe_node(state: State):
    try:
        result = llm.invoke(state["messages"])
        return {"messages": [result]}
    except Exception as e:
        error_msg = {"role": "assistant", "content": f"Error: {str(e)}"}
        return {"messages": [error_msg]}
\`\`\`

## Tool Integration
Tools extend LLM capabilities. Use \`@tool\` decorator for custom tools, bind to LLM, create ToolNode, and add conditional routing.

\`\`\`python
# External tool
from langchain_tavily import TavilySearch
search_tool = TavilySearch(max_results=2)

# Custom tool
@tool
def custom_tool(param: str) -> str:
    """Tool description."""
    return f"Result: {param}"

# Tool list and binding
tools = [search_tool, custom_tool]
llm_with_tools = llm.bind_tools(tools)

# Tool node
tool_node = ToolNode(tools=tools)
graph_builder.add_node("tools", tool_node)

# Conditional routing to tools
graph_builder.add_conditional_edges("chatbot", tools_condition)
graph_builder.add_edge("tools", "chatbot")
\`\`\`

## Memory/Persistence
Checkpointers save state after each step, enabling conversation memory and pause/resume. Each conversation needs a unique \`thread_id\`.

\`\`\`python
# Basic memory
from langgraph.checkpoint.memory import MemorySaver
memory = MemorySaver()
graph = graph_builder.compile(checkpointer=memory)

# Thread-based conversations
config = {"configurable": {"thread_id": "unique_id"}}

# Usage with memory
events = graph.stream(input_data, config, stream_mode="values")

# State inspection
snapshot = graph.get_state(config)
current_state = snapshot.values
next_node = snapshot.next

# Manual state update
graph.update_state(config, {"field": "new_value"})
\`\`\`

## Human-in-the-Loop
Use \`interrupt()\` in tools to pause execution for human input. Resume with \`Command(resume=data)\`.

\`\`\`python
# Interrupt tool
@tool
def human_assistance(query: str) -> str:
    """Request human assistance."""
    human_response = interrupt({"query": query})
    return human_response["data"]

# Usage pattern
# 1. Start execution - hits interrupt
events = graph.stream(input_data, config, stream_mode="values")

# 2. Resume with human input
human_command = Command(resume={"data": "human response"})
events = graph.stream(human_command, config, stream_mode="values")

# Tool with state updates
@tool
def validation_tool(
    name: str, 
    email: str, 
    tool_call_id: Annotated[str, InjectedToolCallId]
) -> str:
    """Validate with human and update state."""
    human_response = interrupt({"name": name, "email": email})
    
    state_update = {
        "validated_name": human_response.get("name", name),
        "validated_email": human_response.get("email", email),
        "messages": [ToolMessage("Validated", tool_call_id=tool_call_id)]
    }
    return Command(update=state_update)
\`\`\`

## Conditional Routing
\`\`\`python
# Using tools_condition (prebuilt)
graph_builder.add_conditional_edges("chatbot", tools_condition)

# Custom conditional function
def custom_router(state: State) -> str:
    last_message = state["messages"][-1]
    if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
        return "tools"
    elif "END" in last_message.content:
        return END
    return "continue"

# Usage
graph_builder.add_conditional_edges(
    "chatbot",
    custom_router,
    {
        "tools": "tools",
        "continue": "chatbot",
        END: END
    }
)
\`\`\`

## Execution Patterns
\`\`\`python
# Basic execution
result = graph.invoke({"messages": [{"role": "user", "content": "Hello"}]})

# Streaming execution
for event in graph.stream(input_data, config, stream_mode="values"):
    print(event)

# With memory/config
config = {"configurable": {"thread_id": "1"}}
events = graph.stream(input_data, config, stream_mode="values")
for event in events:
    if "messages" in event:
        event["messages"][-1].pretty_print()
\`\`\`

## Complete Examples

### Basic Chatbot
\`\`\`python
from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages

class State(TypedDict):
    messages: Annotated[list, add_messages]

graph_builder = StateGraph(State)

def chatbot(state: State):
    return {"messages": [llm.invoke(state["messages"])]}

graph_builder.add_node("chatbot", chatbot)
graph_builder.add_edge(START, "chatbot")
graph_builder.add_edge("chatbot", END)
graph = graph_builder.compile()
\`\`\`

### Tool-Enabled Agent
\`\`\`python
from langchain_tavily import TavilySearch
from langgraph.prebuilt import ToolNode, tools_condition

class State(TypedDict):
    messages: Annotated[list, add_messages]

tool = TavilySearch(max_results=2)
tools = [tool]
llm_with_tools = llm.bind_tools(tools)

graph_builder = StateGraph(State)

def chatbot(state: State):
    return {"messages": [llm_with_tools.invoke(state["messages"])]}

graph_builder.add_node("chatbot", chatbot)
tool_node = ToolNode(tools=tools)
graph_builder.add_node("tools", tool_node)

graph_builder.add_conditional_edges("chatbot", tools_condition)
graph_builder.add_edge("tools", "chatbot")
graph_builder.add_edge(START, "chatbot")

graph = graph_builder.compile()
\`\`\`

### Agent with Memory and Human-in-the-Loop
\`\`\`python
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command, interrupt

@tool
def human_assistance(query: str) -> str:
    human_response = interrupt({"query": query})
    return human_response["data"]

tools = [TavilySearch(max_results=2), human_assistance]
llm_with_tools = llm.bind_tools(tools)

def chatbot(state: State):
    message = llm_with_tools.invoke(state["messages"])
    assert len(message.tool_calls) <= 1  # No parallel tools with interrupts
    return {"messages": [message]}

graph_builder = StateGraph(State)
graph_builder.add_node("chatbot", chatbot)
graph_builder.add_node("tools", ToolNode(tools=tools))
graph_builder.add_conditional_edges("chatbot", tools_condition)
graph_builder.add_edge("tools", "chatbot")
graph_builder.add_edge(START, "chatbot")

memory = MemorySaver()
graph = graph_builder.compile(checkpointer=memory)

# Usage with interrupts
config = {"configurable": {"thread_id": "1"}}
events = graph.stream(input_data, config)  # Pauses at interrupt
human_command = Command(resume={"data": "response"})
events = graph.stream(human_command, config)  # Resumes
\`\`\`

### Custom State Management
\`\`\`python
class CustomState(TypedDict):
    messages: Annotated[list, add_messages]
    user_name: str
    validated: bool

@tool
def validate_user(
    name: str, 
    tool_call_id: Annotated[str, InjectedToolCallId]
) -> str:
    human_response = interrupt({"name": name})
    state_update = {
        "user_name": human_response.get("name", name),
        "validated": True,
        "messages": [ToolMessage("Validated", tool_call_id=tool_call_id)]
    }
    return Command(update=state_update)

# Standard graph setup with CustomState...
\`\`\`

## Time Travel and State History
LangGraph's checkpointing enables time travel - rewinding to previous states and exploring alternative paths.

\`\`\`python
# Get state history
for state in graph.get_state_history(config):
    print(f"Messages: {len(state.values['messages'])}, Next: {state.next}")
    if len(state.values["messages"]) == 6:  # Select specific state
        target_state = state

# Resume from specific checkpoint using checkpoint_id
events = graph.stream(None, target_state.config, stream_mode="values")

# Manual state inspection
snapshot = graph.get_state(config)
current_state = snapshot.values
next_node = snapshot.next
checkpoint_id = snapshot.config["configurable"]["checkpoint_id"]
\`\`\`

## Workflow Patterns

### Prompt Chaining
Sequential LLM calls where each processes the output of the previous one.

\`\`\`python
class State(TypedDict):
    topic: str
    joke: str
    improved_joke: str

def generate_joke(state: State):
    msg = llm.invoke(f"Write a joke about {state['topic']}")
    return {"joke": msg.content}

def improve_joke(state: State):
    msg = llm.invoke(f"Make this funnier: {state['joke']}")
    return {"improved_joke": msg.content}

# Add conditional logic
def check_quality(state: State) -> str:
    return "improve" if "?" not in state["joke"] else "end"

graph_builder.add_conditional_edges(
    "generate_joke", 
    check_quality, 
    {"improve": "improve_joke", "end": END}
)
\`\`\`

### Parallelization
Multiple LLMs work simultaneously, then results are aggregated.

\`\`\`python
class State(TypedDict):
    topic: str
    joke: str
    story: str
    poem: str
    combined: str

def call_llm_joke(state: State):
    msg = llm.invoke(f"Write a joke about {state['topic']}")
    return {"joke": msg.content}

def call_llm_story(state: State):
    msg = llm.invoke(f"Write a story about {state['topic']}")
    return {"story": msg.content}

def aggregator(state: State):
    combined = f"Story: {state['story']}\\nJoke: {state['joke']}"
    return {"combined": combined}

# Multiple edges from START for parallel execution
graph_builder.add_edge(START, "call_llm_joke")
graph_builder.add_edge(START, "call_llm_story")
graph_builder.add_edge("call_llm_joke", "aggregator")
graph_builder.add_edge("call_llm_story", "aggregator")
\`\`\`

### Routing
Classify input and direct to specialized follow-up tasks.

\`\`\`python
from typing_extensions import Literal

class Route(BaseModel):
    choice: Literal["poem", "story", "joke"] = Field(description="Route choice")

router = llm.with_structured_output(Route)

def route_input(state: State):
    decision = router.invoke([
        SystemMessage("Route to poem, story, or joke based on request"),
        HumanMessage(state["input"])
    ])
    return {"decision": decision.choice}

def route_decision(state: State) -> str:
    if state["decision"] == "poem":
        return "poem_node"
    elif state["decision"] == "story":
        return "story_node"
    return "joke_node"

graph_builder.add_conditional_edges(
    "route_input",
    route_decision,
    {"poem_node": "poem_node", "story_node": "story_node", "joke_node": "joke_node"}
)
\`\`\`

### Orchestrator-Worker Pattern
Central orchestrator breaks down tasks and delegates to workers using Send API.

\`\`\`python
from langgraph.constants import Send
from typing import Annotated, List
import operator

class Section(BaseModel):
    name: str
    description: str

class State(TypedDict):
    topic: str
    sections: list[Section]
    completed_sections: Annotated[list, operator.add]  # Workers write here
    final_report: str

class WorkerState(TypedDict):
    section: Section
    completed_sections: Annotated[list, operator.add]

def orchestrator(state: State):
    """Plan the work and create sections"""
    sections = planner.invoke(f"Plan sections for {state['topic']}")
    return {"sections": sections.sections}

def worker(state: WorkerState):
    """Worker writes one section"""
    content = llm.invoke(f"Write section: {state['section'].name}")
    return {"completed_sections": [content.content]}

def assign_workers(state: State):
    """Create workers dynamically using Send API"""
    return [Send("worker", {"section": s}) for s in state["sections"]]

graph_builder.add_conditional_edges("orchestrator", assign_workers, ["worker"])
\`\`\`

### Evaluator-Optimizer
One LLM generates responses while another evaluates and provides feedback in a loop.

\`\`\`python
class Feedback(BaseModel):
    grade: Literal["good", "bad"] = Field(description="Quality assessment")
    feedback: str = Field(description="Improvement suggestions")

evaluator = llm.with_structured_output(Feedback)

def generator(state: State):
    prompt = f"Write about {state['topic']}"
    if state.get("feedback"):
        prompt += f" considering: {state['feedback']}"
    msg = llm.invoke(prompt)
    return {"content": msg.content}

def evaluate(state: State):
    grade = evaluator.invoke(f"Evaluate: {state['content']}")
    return {"grade": grade.grade, "feedback": grade.feedback}

def should_continue(state: State) -> str:
    return "generator" if state["grade"] == "bad" else END

graph_builder.add_conditional_edges(
    "evaluate", 
    should_continue, 
    {"generator": "generator", END: END}
)
\`\`\`

## Prebuilt Agent Components
LangGraph provides prebuilt components for common agent patterns.

\`\`\`python
from langgraph.prebuilt import create_react_agent
from langchain_core.tools import tool

@tool
def calculator(expression: str) -> float:
    """Evaluate mathematical expressions."""
    return eval(expression)

# Prebuilt ReAct agent
agent = create_react_agent(llm, tools=[calculator])

# Usage
response = agent.invoke({"messages": "What is 15 * 23?"})
\`\`\`

## Agent Execution Patterns

### Basic Execution
\`\`\`python
# Synchronous
response = agent.invoke({"messages": "Hello"})

# Asynchronous  
response = await agent.ainvoke({"messages": "Hello"})

# Streaming
for chunk in agent.stream({"messages": "Hello"}):
    print(chunk)

# Async streaming
async for chunk in agent.astream({"messages": "Hello"}):
    print(chunk)
\`\`\`

### Input Formats
\`\`\`python
# String input (converted to HumanMessage)
agent.invoke({"messages": "Hello"})

# Message dictionary
agent.invoke({"messages": {"role": "user", "content": "Hello"}})

# List of messages
agent.invoke({"messages": [{"role": "user", "content": "Hello"}]})

# With custom state fields
agent.invoke({
    "messages": "Hello",
    "user_name": "Alice",
    "context": {"key": "value"}
})
\`\`\`

### Recursion Limits
\`\`\`python
from langgraph.errors import GraphRecursionError

# Set recursion limit to prevent infinite loops
max_iterations = 5
recursion_limit = 2 * max_iterations + 1

try:
    response = agent.invoke(
        {"messages": "Complex task"},
        {"recursion_limit": recursion_limit}
    )
except GraphRecursionError:
    print("Agent stopped due to max iterations")

# Or configure at agent level
agent_with_limit = agent.with_config(recursion_limit=recursion_limit)
\`\`\`

## Advanced Features

### Multi-Agent Systems
\`\`\`python
# Supervisor pattern - coordinates multiple specialist agents
# Swarm pattern - agents collaborate dynamically
# Use langgraph-supervisor and langgraph-swarm packages
\`\`\`

### Memory Integration
\`\`\`python
# Short-term memory (session-based)
# Long-term memory (persistent across sessions)
# Use langmem package for advanced memory features
\`\`\`

### Deployment and Monitoring
\`\`\`python
# LangGraph Platform for production deployment
# LangGraph Studio for visual debugging
# Built-in streaming and human-in-the-loop capabilities
\`\`\`

## Key Rules
1. **State updates**: Return dict with keys to update
2. **Reducers**: \`add_messages\` appends, others overwrite
3. **Tool interrupts**: Use \`assert len(tool_calls) <= 1\`
4. **Memory**: Requires \`thread_id\` in config
5. **Resume**: Use \`Command(resume=data)\` after interrupt
6. **State from tools**: Use \`Command(update=state_dict)\`
7. **Time travel**: Use \`get_state_history()\` and checkpoint_id for rewinding
8. **Workflow patterns**: Choose based on task structure (sequential, parallel, conditional)
9. **Send API**: Use for dynamic worker creation in orchestrator patterns
10. **Recursion limits**: Set to prevent infinite loops in agent execution`;