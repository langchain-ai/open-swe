import { END, START, StateGraph } from "@langchain/langgraph";
import { GraphConfiguration } from "@openswe/shared/open-swe/types";
import { DesignGraphStateObj } from "@openswe/shared/open-swe/design/types";
import {
  designAgent,
  handoffToPlanner,
  classifyDesignIntent,
} from "./nodes/index.js";

/**
 * Design Graph - A dedicated graph for feature graph design conversations.
 *
 * This graph runs in isolation from the manager and planner graphs to:
 * 1. Prevent "thread busy" errors when kicking off development
 * 2. Enable focused, iterative feature design conversations
 * 3. Allow clean handoff to planner without thread conflicts
 *
 * Flow:
 * START → classify-design-intent →
 *   - design-agent (for feature design conversations) → END
 *   - handoff-to-planner (when ready to develop) → END
 *   - END (when design session is complete)
 */
const workflow = new StateGraph(DesignGraphStateObj, GraphConfiguration)
  .addNode("classify-design-intent", classifyDesignIntent, {
    ends: [END, "design-agent", "handoff-to-planner"],
  })
  .addNode("design-agent", designAgent, {
    ends: [END],
  })
  .addNode("handoff-to-planner", handoffToPlanner, {
    ends: [END],
  })
  .addEdge(START, "classify-design-intent")
  .addEdge("classify-design-intent", "design-agent")
  .addEdge("classify-design-intent", "handoff-to-planner")
  .addEdge("classify-design-intent", END)
  .addEdge("design-agent", END)
  .addEdge("handoff-to-planner", END);

export const graph = workflow.compile();
graph.name = "Open SWE - Design";
