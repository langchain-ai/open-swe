import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useEffect } from "react"

import { agentsApi } from "./api"
import { addPendingPrompt } from "./pendingPrompts"
import type { ScheduleUpdateRequest } from "./api"
import type { AgentThread, ImageChunk } from "./types"

export const agentThreadKeys = {
  all: ["agent-threads"] as const,
  detail: (threadId: string) => ["agent-threads", threadId] as const,
}

export const agentScheduleKeys = {
  all: ["agent-schedules"] as const,
}

// The list endpoint (`GET /threads`) and the detail endpoint
// (`GET /threads/{id}`) return the same per-thread summary, so warming the
// detail cache from the already-fetched list avoids a fan-out of one request
// per sidebar thread. Navigation stays instant; the real (mark-viewed) fetch
// fires only when a thread is actually opened. The active thread is skipped so
// its live detail query stays the source of truth.
export function useSeedAgentThreadDetails(
  threads: Array<AgentThread>,
  activeThreadId?: string
) {
  const queryClient = useQueryClient()

  useEffect(() => {
    for (const thread of threads) {
      if (thread.id === activeThreadId) continue
      queryClient.setQueryData(agentThreadKeys.detail(thread.id), thread)
    }
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
      addPendingPrompt(thread.id, variables.prompt, thread.messages.length, {
        images: variables.images,
        modelId: variables.model_id,
        effort: variables.effort,
      })
      queryClient.setQueryData(agentThreadKeys.detail(thread.id), {
        ...thread,
        status: thread.status === "idle" ? "running" : thread.status,
      })
      queryClient.invalidateQueries({ queryKey: agentThreadKeys.all, exact: true })
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

export function useCancelAgentThread(threadId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () => agentsApi.cancelThread(threadId),
    onSuccess: (thread) => {
      queryClient.setQueryData(agentThreadKeys.detail(threadId), thread)
      queryClient.invalidateQueries({ queryKey: agentThreadKeys.all, exact: true })
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
      queryClient.invalidateQueries({ queryKey: agentThreadKeys.all, exact: true })
      const path = window.location.pathname
      if (path.includes(`/agents/${threadId}`)) {
        navigate({ to: "/agents" })
      }
    },
  })
}
