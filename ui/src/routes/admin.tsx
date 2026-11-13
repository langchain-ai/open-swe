import { createFileRoute } from "@tanstack/react-router"

import { SettingsPage } from "@/components/AppShell"
import { ExpeditedReviewSection } from "@/features/settings/components/ExpeditedReviewSection"
import { FeaturedAppsSection } from "@/features/settings/components/FeaturedAppsSettings"
import { ManagedToolsGatewaySection } from "@/features/settings/components/ManagedToolsGatewaySection"
import { ReviewSettings } from "@/features/settings/components/ReviewSettings"
import { ShadowReviewSection } from "@/features/settings/components/ShadowReviewSection"
import {
  DefaultRepoSection,
  LLMGatewaySection,
  ModelDefaultsSection,
  ModelRoutingSection,
} from "@/features/settings/components/WorkspaceSettingsSections"
import { INSTANCE_SCOPE } from "@/features/settings/lib/settingsScope"
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
      description="Settings every environment inherits. Open an environment to override one there."
    >
      <ModelDefaultsSection
        scope={INSTANCE_SCOPE}
        models={(modelOptions.data?.models ?? []).filter(
          (model) => model.can_be_default !== false
        )}
      />
      <ModelRoutingSection
        scope={INSTANCE_SCOPE}
        models={(modelOptions.data?.models ?? []).filter(
          (model) => model.can_be_default !== false
        )}
      />
      <DefaultRepoSection
        scope={INSTANCE_SCOPE}
        repositories={repos.data?.repositories ?? []}
      />
      <LLMGatewaySection scope={INSTANCE_SCOPE} />
      <ReviewSettings scope={INSTANCE_SCOPE} canEdit />
      <ExpeditedReviewSection scope={INSTANCE_SCOPE} />
      <ShadowReviewSection
        scope={INSTANCE_SCOPE}
        models={(modelOptions.data?.models ?? []).filter(
          (model) => model.can_be_default !== false
        )}
      />
      <ManagedToolsGatewaySection />
      <FeaturedAppsSection />
    </SettingsPage>
  )
}
