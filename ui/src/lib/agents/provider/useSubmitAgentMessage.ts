import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useStreamContext as useAgentThreadStream, type UseStreamReturn } from "@langchain/react";

import type { SendAgentMessageVariables } from "@/lib/agents/queries";
import { AgentsApiError, agentsApi } from "@/lib/agents/api";
import { agentThreadKeys } from "@/lib/agents/queries";
import type { ImageChunk } from "@/lib/agents/types";

function runConfig(vars: SendAgentMessageVariables) {
  if (!vars.model_id || !vars.effort) return undefined;
  return {
    configurable: {
      agent_model_id: vars.model_id,
      agent_effort: vars.effort,
    },
  };
}

function imageContentBlocks(images: Array<ImageChunk> = []) {
  return images.map((image) => ({
    type: "image",
    base64: image.base64,
    mime_type: image.mimeType,
    ...(image.fileName ? { file_name: image.fileName } : {}),
  }));
}

function messageContent(vars: SendAgentMessageVariables) {
  const text = vars.content.trim();
  const imageBlocks = imageContentBlocks(vars.images);
  if (imageBlocks.length === 0) return text;
  return [...imageBlocks, ...(text ? [{ type: "text", text }] : [])];
}

async function submitRun(stream: UseStreamReturn, vars: SendAgentMessageVariables) {
  await stream.submit(
    { messages: [{ type: "human", content: messageContent(vars) }] },
    { config: runConfig(vars) },
  );
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

      await submitRun(stream, vars);
    },
    onSuccess: () => {
      queryClient.setQueryData(agentThreadKeys.detail(threadId), (prev) =>
        prev ? { ...prev, status: "running" as const } : prev,
      );
      void queryClient.invalidateQueries({ queryKey: agentThreadKeys.all, exact: true });
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
  await submitRun(stream, vars);
}
