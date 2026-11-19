import { createFileRoute } from "@tanstack/react-router"

import { LegacySettingsRedirect } from "@/features/settings/components/LegacySettingsRedirect"

export const Route = createFileRoute("/workspaces")({
  component: LegacySettingsRedirect,
})
