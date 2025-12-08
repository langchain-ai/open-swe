import { v4 as uuidv4 } from "uuid";
import { START } from "@langchain/langgraph/web";
import { Client, StreamMode, ThreadState } from "@langchain/langgraph-sdk";
import { MANAGER_GRAPH_ID, OPEN_SWE_STREAM_MODE } from "@openswe/shared/constants";
import { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";
import { PlannerGraphState } from "@openswe/shared/open-swe/planner/types";
import {
  AgentSession,
  GraphConfig,
  GraphState,
  InteractionPhase,
} from "@openswe/shared/open-swe/types";
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

  const threadPhase =
    inputs.threadConfig?.configurable?.phase ??
    (inputs.threadConfig as
      | (GraphConfig & {
          metadata?: { configurable?: { phase?: InteractionPhase } };
        })
      | undefined)?.metadata?.configurable?.phase ??
    (inputs.threadState.metadata as
      | { configurable?: { phase?: InteractionPhase } }
      | undefined)?.configurable?.phase;

  const configurable = {
    ...getCustomConfigurableFields(inputs.threadConfig),
    ...(inputs.graphId === MANAGER_GRAPH_ID && threadPhase
      ? { phase: threadPhase }
      : {}),
  } satisfies GraphConfig["configurable"];

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
      configurable,
    },
  });
  return {
    threadId: newThreadId,
    runId: run.run_id,
  };
}
