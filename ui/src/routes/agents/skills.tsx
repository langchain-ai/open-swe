import { createFileRoute, redirect } from "@tanstack/react-router"

export const Route = createFileRoute("/agents/skills")({
  beforeLoad: () => {
    throw redirect({ to: "/plugins", search: { tab: "skills" } })
  },
})
