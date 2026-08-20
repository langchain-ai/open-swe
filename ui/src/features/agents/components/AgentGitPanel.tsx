import { forwardRef } from "react"

import type { AgentThread } from "@/features/agents/lib/types"
import {
  RightPanelHost,
  type RightPanelHostHandle,
} from "@/features/agents/components/RightPanelHost"

interface AgentGitPanelProps {
  thread: AgentThread
  revealFilePath?: string | null
  revealChangesKey?: number
  collapsed: boolean
  onCollapsedChange: (next: boolean) => void
}

export const AgentGitPanel = forwardRef<
  RightPanelHostHandle,
  AgentGitPanelProps
>(function AgentGitPanel(
  {
    thread,
    revealFilePath,
    revealChangesKey = 0,
    collapsed,
    onCollapsedChange,
  },
  ref
) {
  return (
    <RightPanelHost
      ref={ref}
      target={{ kind: "cloud", thread }}
      collapsed={collapsed}
      onCollapsedChange={onCollapsedChange}
      isRunning={thread.status === "running"}
      revealFilePath={revealFilePath}
      revealDiffKey={revealChangesKey}
    />
  )
})
