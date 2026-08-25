import { createFileRoute } from "@tanstack/react-router"

import { SkillsPage } from "@/features/agents/components/SkillsPage"

export const Route = createFileRoute("/agents/skills")({
  component: SkillsPage,
})
