import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";

import { agentsApi } from "./api";

export const agentThreadKeys = {
  all: ["agent-threads"] as const,
  detail: (threadId: string) => ["agent-threads", threadId] as const,
};

export function useAgentThreads() {
  return useQuery({
    queryKey: agentThreadKeys.all,
    queryFn: () => agentsApi.listThreads(),
  });
}

export function useAgentThread(threadId: string) {
  return useQuery({
    queryKey: agentThreadKeys.detail(threadId),
    queryFn: () => agentsApi.getThread(threadId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "running" ? 2000 : false;
    },
  });
}

export function useCreateAgentThread() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  return useMutation({
    mutationFn: agentsApi.createThread,
    onSuccess: (thread) => {
      queryClient.invalidateQueries({ queryKey: agentThreadKeys.all });
      navigate({ to: "/agents/$threadId", params: { threadId: thread.id } });
    },
  });
}

export function useSendAgentMessage(threadId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (content: string) => agentsApi.sendMessage(threadId, { content }),
    onSuccess: (thread) => {
      queryClient.setQueryData(agentThreadKeys.detail(threadId), thread);
      queryClient.invalidateQueries({ queryKey: agentThreadKeys.all });
    },
  });
}

export function useCancelAgentThread(threadId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => agentsApi.cancelThread(threadId),
    onSuccess: (thread) => {
      queryClient.setQueryData(agentThreadKeys.detail(threadId), thread);
      queryClient.invalidateQueries({ queryKey: agentThreadKeys.all });
    },
  });
}

export function useDeleteAgentThread() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  return useMutation({
    mutationFn: (threadId: string) => agentsApi.deleteThread(threadId),
    onSuccess: (_, threadId) => {
      queryClient.removeQueries({ queryKey: agentThreadKeys.detail(threadId) });
      queryClient.invalidateQueries({ queryKey: agentThreadKeys.all });
      const path = window.location.pathname;
      if (path.includes(`/agents/${threadId}`)) {
        navigate({ to: "/agents" });
      }
    },
  });
}
