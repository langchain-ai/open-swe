import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useEffect } from "react"

import { agentsApi } from "./api"
import { addPendingPrompt } from "./pendingPrompts"
import type { ScheduleUpdateRequest } from "./api"
import type { ImageChunk } from "./types"

export const agentThreadKeys = {
  all: ["agent-threads"] as const,
  detail: (threadId: string) => ["agent-threads", threadId] as const,
}

export const agentScheduleKeys = {
  all: ["agent-schedules"] as const,
}

const PREFETCH_THREAD_DETAIL_LIMIT = 12

export function usePrefetchAgentThreadDetails(
  threads: Array<{ id: string }>,
  activeThreadId?: string
) {
  const queryClient = useQueryClient()

  useEffect(() => {
    const threadIds = threads
      .map((thread) => thread.id)
      .filter((threadId) => threadId !== activeThreadId)
      .slice(0, PREFETCH_THREAD_DETAIL_LIMIT)

    threadIds.forEach((threadId) => {
      void queryClient.prefetchQuery({
        queryKey: agentThreadKeys.detail(threadId),
        queryFn: () => agentsApi.getThread(threadId, { markViewed: false }),
      })
    })
  }, [activeThreadId, queryClient, threads])
}

export function useAgentThreads() {
  return useQuery({
    queryKey: agentThreadKeys.all,
    queryFn: () => agentsApi.listThreads(),
    refetchInterval: (query) =>
      query.state.data?.some((thread) => thread.status === "running")
        ? 2000
        : false,
  })
}

export function useAgentThread(threadId: string) {
  return useQuery({
    queryKey: agentThreadKeys.detail(threadId),
    queryFn: () => agentsApi.getThread(threadId),
    refetchOnMount: "always",
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === "running" ? 2000 : false
    },
  })
}

export function useAgentSchedules() {
  return useQuery({
    queryKey: agentScheduleKeys.all,
    queryFn: () => agentsApi.listSchedules(),
  })
}

export function useCreateAgentSchedule() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: agentsApi.createSchedule,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentScheduleKeys.all })
    },
  })
}

export function useUpdateAgentSchedule() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (vars: { scheduleId: string; body: ScheduleUpdateRequest }) =>
      agentsApi.updateSchedule(vars.scheduleId, vars.body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentScheduleKeys.all })
    },
  })
}

export function useDeleteAgentSchedule() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: agentsApi.deleteSchedule,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentScheduleKeys.all })
    },
  })
}

export function useCreateAgentThread() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  return useMutation({
    mutationFn: agentsApi.createThread,
    onSuccess: (thread, variables) => {
      addPendingPrompt(
        thread.id,
        variables.prompt,
        thread.messages.length,
        variables.images
      )
      queryClient.setQueryData(agentThreadKeys.detail(thread.id), {
        ...thread,
        status: thread.status === "idle" ? "running" : thread.status,
      })
      queryClient.invalidateQueries({ queryKey: agentThreadKeys.all })
      navigate({ to: "/agents/$threadId", params: { threadId: thread.id } })
    },
  })
}

export interface SendAgentMessageVariables {
  content: string
  images?: Array<ImageChunk>
  model_id?: string | null
  effort?: string | null
}

export function useSendAgentMessage(threadId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (vars: SendAgentMessageVariables) =>
      agentsApi.sendMessage(threadId, {
        content: vars.content,
        images: vars.images,
        model_id: vars.model_id,
        effort: vars.effort,
      }),
    onMutate: (vars) => {
      const cached = queryClient.getQueryData<{ messages?: Array<unknown> }>(
        agentThreadKeys.detail(threadId)
      )
      const insertAt = Array.isArray(cached?.messages)
        ? cached.messages.length
        : 0
      addPendingPrompt(threadId, vars.content, insertAt, vars.images)
    },
    onSuccess: (thread) => {
      queryClient.setQueryData(
        agentThreadKeys.detail(threadId),
        (prev: typeof thread | undefined) => {
          if (!prev) return thread
          return {
            ...thread,
            messages:
              thread.messages.length > 0 ? thread.messages : prev.messages,
          }
        }
      )
      queryClient.invalidateQueries({ queryKey: agentThreadKeys.all })
    },
  })
}

export function useCancelAgentThread(threadId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () => agentsApi.cancelThread(threadId),
    onSuccess: (thread) => {
      queryClient.setQueryData(agentThreadKeys.detail(threadId), thread)
      queryClient.invalidateQueries({ queryKey: agentThreadKeys.all })
    },
  })
}

export function useDeleteAgentThread() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  return useMutation({
    mutationFn: (threadId: string) => agentsApi.deleteThread(threadId),
    onSuccess: (_, threadId) => {
      queryClient.removeQueries({ queryKey: agentThreadKeys.detail(threadId) })
      queryClient.invalidateQueries({ queryKey: agentThreadKeys.all })
      const path = window.location.pathname
      if (path.includes(`/agents/${threadId}`)) {
        navigate({ to: "/agents" })
      }
    },
  })
}
