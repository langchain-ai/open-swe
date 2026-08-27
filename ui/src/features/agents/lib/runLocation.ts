import { useQuery } from "@tanstack/react-query"

import type { AgentThread } from "@/features/agents/lib/types"
import type { DesktopDeviceIdentity } from "@/desktop"

export function isLocalThread(thread: {
  runLocation?: string | null
}): boolean {
  return thread.runLocation === "local"
}

export function useDeviceIdentity() {
  return useQuery({
    queryKey: ["desktop-device-identity"],
    queryFn: async (): Promise<DesktopDeviceIdentity | null> =>
      (await window.openSweDesktop?.deviceIdentity()) ?? null,
    enabled: typeof window !== "undefined" && Boolean(window.openSweDesktop),
    staleTime: Infinity,
  })
}

/**
 * Whether this client can drive the thread's agent.
 *
 * A local thread's working tree only exists on the machine that created it, so
 * a run started from anywhere else — the web, or a second install — would have
 * nowhere to execute. Those clients still render the transcript.
 */
export function canRunThread(
  thread: Pick<AgentThread, "runLocation" | "deviceId">,
  deviceId: string | null | undefined
): boolean {
  if (!isLocalThread(thread)) return true
  return Boolean(deviceId) && thread.deviceId === deviceId
}

export function runsElsewhereLabel(
  thread: Pick<AgentThread, "deviceName" | "deviceId">
): string {
  return `Runs on ${thread.deviceName || "another computer"}`
}
