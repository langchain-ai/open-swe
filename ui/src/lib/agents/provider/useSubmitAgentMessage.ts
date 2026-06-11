import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useStreamContext as useAgentThreadStream } from "@langchain/react";

import type { SendAgentMessageVariables } from "@/lib/agents/queries";
import { AgentsApiError, agentsApi } from "@/lib/agents/api";
import { agentThreadKeys } from "@/lib/agents/queries";

/**
 * Construct the message content for the LangGraph run.
 *
 * @param vars - The variables for the message.
 * @returns The message content.
 */
function messageContent(vars: SendAgentMessageVariables) {
  const text = vars.content.trim();
  const imageBlocks = vars.images?.map((image) => ({
    type: "image",
    base64: image.base64,
    mime_type: image.mimeType,
    ...(image.fileName ? { file_name: image.fileName } : {}),
  })) ?? [];
  return [...imageBlocks, ...(text ? [{ type: "text", text }] : [])];
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
 * 
 * @param threadId - The ID of the thread to submit the message to.
 * @returns The mutation object.
 */
export function useSubmitAgentMessage(threadId: string) {
  const queryClient = useQueryClient();
  const stream = useAgentThreadStream();

  return useMutation({
    mutationFn: async (vars: SendAgentMessageVariables) => {
      const queue = () =>
        agentsApi.queueMessage(threadId, {
          content: vars.content,
          images: vars.images,
          model_id: vars.model_id,
          effort: vars.effort,
        });

      if (stream.isLoading) {
        await queue();
        return;
      }

      try {
        await queue();
        return;
      } catch (error) {
        if (!(error instanceof AgentsApiError) || error.status !== 409) {
          throw error;
        }
      }

      const config = (!vars.model_id || !vars.effort)
        ? undefined
        : {
          configurable: {
            agent_model_id: vars.model_id,
            agent_effort: vars.effort,
          },
        };

      await stream.submit(
        { messages: [{ type: "human", content: messageContent(vars) }] },
        { config },
      );
    },
    onSuccess: () => {
      queryClient.setQueryData(agentThreadKeys.detail(threadId), (prev) =>
        prev ? { ...prev, status: "running" as const } : prev,
      );
      void queryClient.invalidateQueries({ queryKey: agentThreadKeys.all, exact: true });
    },
  });
}
