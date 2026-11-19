import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { ExecutionDefaultsSettings } from "@/features/settings/components/ExecutionDefaultsSettings"
import { ExpeditedReviewSection } from "@/features/settings/components/ExpeditedReviewSection"
import { FeaturedAppsSection } from "@/features/settings/components/FeaturedAppsSettings"
import { ReviewSettings } from "@/features/settings/components/ReviewSettings"
import { ShadowReviewSection } from "@/features/settings/components/ShadowReviewSection"
import {
  DefaultRepoSection,
  ModelDefaultsSection,
  ModelRoutingSection,
} from "@/features/settings/components/WorkspaceSettingsSections"
import { useOptions, useRepos } from "@/lib/profile"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/admin")({
  head: () => ({ meta: [{ title: pageTitle("Defaults") }] }),
  component: AdminDefaultsPage,
})

function AdminDefaultsPage() {
  const modelOptions = useOptions()
  const repos = useRepos()
  return (
    <SettingsPage
      adminOnly
      title="Defaults"
      description="Robin-wide execution defaults, model routing, and review settings."
    >
      <ExecutionDefaultsSettings
        repositories={repos.data?.repositories ?? []}
      />
      <ModelDefaultsSection
        models={(modelOptions.data?.models ?? []).filter(
          (model) => model.can_be_default !== false
        )}
      />
      <ModelRoutingSection
        models={(modelOptions.data?.models ?? []).filter(
          (model) => model.can_be_default !== false
        )}
      />
      <DefaultRepoSection repositories={repos.data?.repositories ?? []} />
      <ReviewSettings canEdit />
      <ExpeditedReviewSection />
      <ShadowReviewSection
        models={(modelOptions.data?.models ?? []).filter(
          (model) => model.can_be_default !== false
        )}
      />
      <FeaturedAppsSection />
    </SettingsPage>
  )
}
