import { createFileRoute } from "@tanstack/react-router"

import { ReviewStylesPanel } from "@/components/ReviewStylesPanel"
import { SettingsPage } from "@/components/AppShell"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/review_/styles")({
  head: () => ({ meta: [{ title: pageTitle("Review styles") }] }),
  component: () => (
    <SettingsPage
      title="Review styles"
      description="Per-repository review style guides and approval policies. Run analysis to learn a style guide from past pull request feedback."
    >
      <div className="rounded-lg border border-border bg-card">
        <ReviewStylesPanel />
      </div>
    </SettingsPage>
  ),
})
