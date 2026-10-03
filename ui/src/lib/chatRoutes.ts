import { useRouterState } from "@tanstack/react-router"

const standard = { home: "/agents", thread: "/agents/$threadId" } as const
const assistant = {
  home: "/assistant",
  thread: "/assistant/$threadId",
} as const
const copilot = { home: "/copilot", thread: "/copilot/$threadId" } as const

function within(pathname: string, home: string) {
  return pathname === home || pathname.startsWith(`${home}/`)
}

export function chatRoutes(pathname: string) {
  if (within(pathname, assistant.home)) return assistant
  if (within(pathname, copilot.home)) return copilot
  return standard
}

export function useChatRoutes() {
  return useRouterState({
    select: (state) => chatRoutes(state.location.pathname),
  })
}
