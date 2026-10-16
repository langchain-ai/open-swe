import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { UsersSection } from "@/features/settings/components/AdminSections"
import { AnnouncementsSection } from "@/features/settings/components/AnnouncementsSection"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/admin_/users")({
  head: () => ({ meta: [{ title: pageTitle("Users") }] }),
  component: () => (
    <SettingsPage
      adminOnly
      title="Users"
      description="Everyone who has signed in, and the Slack account each has connected."
    >
      <AnnouncementsSection />
      <UsersSection enabled />
    </SettingsPage>
  ),
})
