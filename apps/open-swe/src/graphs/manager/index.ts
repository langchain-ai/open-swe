import { END, START, StateGraph } from "@langchain/langgraph";
import { GraphConfiguration } from "@openswe/shared/open-swe/types";
import { ManagerGraphStateObj } from "@openswe/shared/open-swe/manager/types";
import {
  classifyMessage,
  startPlanner,
  createNewSession,
  resolveWorkspace,
  featureGraphAgent,
} from "./nodes/index.js";

const workflow = new StateGraph(ManagerGraphStateObj, GraphConfiguration)
  .addNode("resolve-workspace", resolveWorkspace, {
    ends: ["classify-message"],
  })
  .addNode("classify-message", classifyMessage, {
    ends: [END, "start-planner", "create-new-session", "feature-graph-agent"],
  })
  .addNode("create-new-session", createNewSession)
  .addNode("start-planner", startPlanner)
  .addNode("feature-graph-agent", featureGraphAgent)
  .addEdge(START, "resolve-workspace")
  .addEdge("resolve-workspace", "classify-message")
  .addEdge("create-new-session", END)
  .addEdge("start-planner", END)
  .addEdge("classify-message", "feature-graph-agent")
  .addEdge("feature-graph-agent", END);

export const graph = workflow.compile();
graph.name = "Open SWE - Manager";
