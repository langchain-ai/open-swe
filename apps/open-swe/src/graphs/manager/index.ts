import { END, START, StateGraph } from "@langchain/langgraph";
import { GraphConfiguration } from "@openswe/shared/open-swe/types";
import { ManagerGraphStateObj } from "@openswe/shared/open-swe/manager/types";
import {
  classifyMessage,
  startPlanner,
  createNewSession,
  resolveWorkspace,
  featureGraphAgent,
  featureGraphOrchestrator,
} from "./nodes/index.js";

const workflow = new StateGraph(ManagerGraphStateObj, GraphConfiguration)
  .addNode("resolve-workspace", resolveWorkspace, {
    ends: ["classify-message", "feature-graph-orchestrator"],
  })
  .addNode("feature-graph-orchestrator", featureGraphOrchestrator, {
    ends: [END, "classify-message"],
  })
  .addNode("classify-message", classifyMessage, {
    ends: [
      END,
      "start-planner",
      "create-new-session",
      "feature-graph-agent",
      "feature-graph-orchestrator",
    ],
  })
  .addNode("create-new-session", createNewSession)
  .addNode("start-planner", startPlanner)
  .addNode("feature-graph-agent", featureGraphAgent)
  .addEdge(START, "resolve-workspace")
  .addEdge("resolve-workspace", "feature-graph-orchestrator")
  .addEdge("feature-graph-orchestrator", "classify-message")
  .addEdge("feature-graph-orchestrator", END)
  .addEdge("create-new-session", END)
  .addEdge("start-planner", END)
  .addEdge("classify-message", "feature-graph-agent")
  .addEdge("feature-graph-agent", END);

export const graph = workflow.compile();
graph.name = "Open SWE - Manager";
