import { useRouterState } from "@tanstack/react-router"

/**
 * The `task` tool-call id of the subagent the thread page is showing, from the
 * `?subagent=` search param, or null when it shows the thread itself. Read from
 * the router directly so a sidebar row can highlight its sub-thread without
 * the id being threaded through every list component in between.
 */
export function useActiveSubagentId(): string | null {
  return useRouterState({
    select: (state) => {
      const search: Record<string, unknown> = state.location.search
      const value = search["subagent"]
      return typeof value === "string" && value ? value : null
    },
  })
}
