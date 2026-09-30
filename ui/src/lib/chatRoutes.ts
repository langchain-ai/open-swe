import { useRouterState } from "@tanstack/react-router"

const standard = { home: "/agents", thread: "/agents/$threadId" } as const
const assistant = {
  home: "/assistant",
  thread: "/assistant/$threadId",
} as const

export function chatRoutes(pathname: string) {
  return pathname === "/assistant" || pathname.startsWith("/assistant/")
    ? assistant
    : standard
}

export function useChatRoutes() {
  return useRouterState({
    select: (state) => chatRoutes(state.location.pathname),
  })
}
