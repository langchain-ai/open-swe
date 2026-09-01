import { useEffect } from "react"
import { STREAM_CONTROLLER } from "@langchain/react"

import { useAgentThreadRuntime } from "@/features/agents/lib/AgentThreadStreamProvider"

const RETRY_DELAYS_MS = [250, 1_000, 2_500]

export function OptimisticThreadHydrationRecovery({
  threadId,
  enabled,
}: {
  threadId: string
  enabled: boolean
}) {
  const stream = useAgentThreadRuntime()
  const controller = stream[STREAM_CONTROLLER]

  useEffect(() => {
    if (!enabled || stream.messages.length > 0) return

    let cancelled = false
    let retry = 0
    let timer: ReturnType<typeof setTimeout> | undefined

    const schedule = () => {
      if (cancelled || retry >= RETRY_DELAYS_MS.length) return
      timer = setTimeout(() => {
        if (cancelled) return
        retry += 1
        void controller
          .hydrate(threadId)
          .catch(() => undefined)
          .finally(schedule)
      }, RETRY_DELAYS_MS[retry])
    }

    void stream.hydrationPromise.then(schedule, schedule)
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [
    controller,
    enabled,
    stream.hydrationPromise,
    stream.messages.length,
    threadId,
  ])

  return null
}
