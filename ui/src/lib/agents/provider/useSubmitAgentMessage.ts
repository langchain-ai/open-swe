import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useStreamContext as useAgentThreadStream, type UseStreamReturn } from "@langchain/react";

import type { SendAgentMessageVariables } from "@/lib/agents/queries";
import { agentsApi } from "@/lib/agents/api";
import { agentThreadKeys } from "@/lib/agents/queries";

function runConfig(vars: SendAgentMessageVariables) {
  if (!vars.model_id || !vars.effort) return undefined;
  return {
    configurable: {
      agent_model_id: vars.model_id,
      agent_effort: vars.effort,
    },
  };
}

/**
 * User-initiated sends from the prompt bar. Prefer this over calling `stream.submit`
 * directly so cache updates and the busy-thread queue path stay consistent.
 *
 * When the thread is idle, submits a new run via the stream commands endpoint.
 * When a run is already in flight (`stream.isLoading`), posts to the dashboard
 * `/messages` endpoint instead of using LangGraph `multitaskStrategy: "enqueue"`.
 * That endpoint writes to the thread store; `check_message_queue_before_model`
 * injects the message into the *current* run before the next model call — the
 * same mid-run follow-up path used by Slack, Linear, and GitHub webhooks.
 */
export function useSubmitAgentMessage(threadId: string) {
  const queryClient = useQueryClient();
  const stream = useAgentThreadStream();

  return useMutation({
    mutationFn: async (vars: SendAgentMessageVariables) => {
      if (stream.isLoading) {
        await agentsApi.queueMessage(threadId, {
          content: vars.content,
          images: vars.images,
          model_id: vars.model_id,
          effort: vars.effort,
        });
        return;
      }

      await stream.submit(
        { messages: [{ type: "human", content: vars.content }] },
        { config: runConfig(vars) },
      );
    },
    onSuccess: () => {
      queryClient.setQueryData(agentThreadKeys.detail(threadId), (prev) =>
        prev ? { ...prev, status: "running" as const } : prev,
      );
      void queryClient.invalidateQueries({ queryKey: agentThreadKeys.all });
    },
  });
}

/**
 * Imperative `stream.submit` for effects that already know the stream is idle
 * (e.g. flushing a pending prompt on mount in `AgentThreadView`). Does not
 * queue via `/messages` or touch React Query — use `useSubmitAgentMessage`
 * for interactive sends so busy-thread follow-ups and cache invalidation apply.
 */
export async function submitAgentPrompt(
  stream: UseStreamReturn,
  vars: SendAgentMessageVariables,
) {
  await stream.submit(
    { messages: [{ type: "human", content: vars.content }] },
    { config: runConfig(vars) },
  );
}
