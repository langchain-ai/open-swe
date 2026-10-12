import { useEffect } from "react"
import { useRouterState } from "@tanstack/react-router"
import { api } from "./api"
import { useSession } from "./session"

const pages = new Set([
  "agents",
  "review",
  "usage",
  "workspaces",
  "integrations",
  "admin",
  "assistant",
  "incidents",
  "cloud-agents",
  "feature-flags",
])

export function PageTracking() {
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  const { data: session } = useSession()
  const login = session?.login

  useEffect(() => {
    if (!login) return
    const section = pathname.split("/")[1] || "agents"
    const page =
      section === "my-settings"
        ? "settings"
        : pages.has(section)
          ? section
          : "other"
    void api.recordPageView(page).catch((error: unknown) => {
      console.error("Failed to record page view", error)
    })
  }, [pathname, login])

  return null
}
