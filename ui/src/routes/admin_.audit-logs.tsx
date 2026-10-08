import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { AuditLogs } from "@/features/settings/components/AuditLogs"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/admin_/audit-logs")({
  head: () => ({ meta: [{ title: pageTitle("Audit logs") }] }),
  component: () => (
    <SettingsPage
      adminOnly
      title="Audit logs"
      description="Inspect recorded API and agent-tool activity across this installation."
      className="max-w-6xl"
    >
      <AuditLogs />
    </SettingsPage>
  ),
})
