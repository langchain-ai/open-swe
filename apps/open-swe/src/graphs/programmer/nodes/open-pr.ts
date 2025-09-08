import { v4 as uuidv4 } from "uuid";
import { AIMessage } from "@langchain/core/messages";
import {
  GraphConfig,
  GraphState,
  GraphUpdate,
} from "@openswe/shared/open-swe/types";

export async function openPullRequest(
  state: GraphState,
  _config: GraphConfig,
): Promise<GraphUpdate> {
  const message = new AIMessage({
    id: uuidv4(),
    content: "Pull request operations are not supported in this environment.",
  });
  return {
    messages: [message],
    internalMessages: [message],
    taskPlan: state.taskPlan,
  };
}
