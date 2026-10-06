import type { ReactNode } from "react"
import { Box } from "@langchain/gtm-platform-design-system/ui/box"

/** Bottom dock for the composer, on the thread's 704px measure and its 24px gutter. */
export function AgentComposerDock({ children }: { children: ReactNode }) {
  return (
    <Box
      className="shrink-0 pb-4"
      style={{ viewTransitionName: "agent-composer" }}
    >
      <Box className="mx-auto w-full max-w-thread min-w-0 px-6">{children}</Box>
    </Box>
  )
}
