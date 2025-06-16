import { END, START, StateGraph } from "@langchain/langgraph";
import { GraphConfiguration } from "@open-swe/shared/open-swe/types";
import { ManagerGraphState } from "./types.js";
import {
  initializeGithubIssue,
  classifyMessage,
  startPlanner,
} from "./nodes/index.js";

const workflow = new StateGraph(ManagerGraphState, GraphConfiguration)
  .addNode("initialize-github-issue", initializeGithubIssue)
  .addNode("classify-message", classifyMessage, {
    ends: [END, "start-planner"],
  })
  .addNode("start-planner", startPlanner)
  .addEdge(START, "initialize-github-issue")
  .addEdge("initialize-github-issue", "classify-message")
  .addEdge("start-planner", END);

export const graph = workflow.compile();
graph.name = "Manager";
