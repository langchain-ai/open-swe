import { v4 as uuidv4 } from "uuid";
import { START } from "@langchain/langgraph/web";
import { Client, StreamMode, ThreadState } from "@langchain/langgraph-sdk";
import { OPEN_SWE_STREAM_MODE } from "@openswe/shared/constants";
import { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@openswe/shared/open-swe/planner/types";
import { AgentSession, GraphConfig, GraphState } from "@openswe/shared/open-swe/types";
import { getCustomConfigurableFields } from "@openswe/shared/open-swe/utils/config";

export async function createNewSession(
  client: Client,
  inputs: {
    graphId: string;
    threadState: ThreadState<
      ManagerGraphState | PlannerGraphState | GraphState
    >;
    threadConfig: GraphConfig;
  },
): Promise<AgentSession> {
  const newThreadId = uuidv4();
  const nextNode = inputs.threadState.next[0] ?? START;

  const run = await client.runs.create(newThreadId, inputs.graphId, {
    command: {
      update: inputs.threadState.values,
      goto: nextNode,
    },
    ifNotExists: "create",
    streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
    streamResumable: true,
    config: {
      recursion_limit: 400,
      configurable: getCustomConfigurableFields(inputs.threadConfig),
    },
  });
  return {
    threadId: newThreadId,
    runId: run.run_id,
  };
}
