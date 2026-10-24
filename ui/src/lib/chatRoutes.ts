const routes = { home: "/agents", thread: "/agents/$threadId" } as const

export function chatRoutes() {
  return routes
}

export function useChatRoutes() {
  return routes
}
