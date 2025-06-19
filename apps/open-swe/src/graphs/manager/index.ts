import { END, START, StateGraph } from "@langchain/langgraph";
import { GraphConfiguration } from "@open-swe/shared/open-swe/types";
import { ManagerGraphStateObj } from "@open-swe/shared/open-swe/manager/types";
import {
  initializeGithubIssue,
  classifyMessage,
  startPlanner,
  createNewSession,
} from "./nodes/index.js";

const workflow = new StateGraph(ManagerGraphStateObj, GraphConfiguration)
  .addNode("initialize-github-issue", initializeGithubIssue)
  .addNode("classify-message", classifyMessage, {
    ends: [END, "start-planner", "create-new-session"],
  })
  .addNode("create-new-session", createNewSession)
  .addNode("start-planner", startPlanner)
  .addEdge(START, "initialize-github-issue")
  .addEdge("initialize-github-issue", "classify-message")
  .addEdge("create-new-session", END)
  .addEdge("start-planner", END);

export const graph = workflow.compile();
graph.name = "Open SWE - Manager";
