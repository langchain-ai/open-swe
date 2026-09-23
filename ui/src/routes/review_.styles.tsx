import { createFileRoute } from "@tanstack/react-router"

import { AuthedAppShell } from "@/components/AppShell"
import { ReviewStylesPanel } from "@/components/ReviewStylesPanel"

export const Route = createFileRoute("/review_/styles")({
  component: ReviewStylesPage,
})

function ReviewStylesPage() {
  return (
    <AuthedAppShell
      title="Review Style Prompts"
      description="Customize repository review style and approval policy. Run analysis to learn a style guide from past PR feedback."
      backTo={{ to: "/review", label: "Back to Open SWE Review" }}
    >
      {() => (
        <div className="rounded-lg border border-border bg-card">
          <ReviewStylesPanel />
        </div>
      )}
    </AuthedAppShell>
  )
}
