import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { usePluginHost } from "@openswe/plugin-sdk/react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { useState } from "react"

/**
 * Asks the person to update when a newer Design QA release is out, since few
 * look for updates in Settings → Apps. The web UI confirms the update and
 * reloads the app on it; a web UI that can't tell shows nothing.
 */
export function UpdateBanner() {
  const host = usePluginHost()
  const newer = useQuery({
    queryKey: ["newer-release"],
    queryFn: () => host.app.newerRelease().catch(() => null),
    staleTime: 10 * 60_000,
    refetchInterval: 30 * 60_000,
    refetchOnWindowFocus: true,
  })
  const update = useMutation({ mutationFn: () => host.app.update() })
  const [dismissed, setDismissed] = useState<number | null>(null)
  const release = newer.data
  if (!release || dismissed === release.number) return null

  return (
    <Banner
      intent="info"
      title={`Design QA ${release.version} is out`}
      dismissible
      onDismiss={() => setDismissed(release.number)}
      action={
        <Button
          size="sm"
          loading={update.isPending}
          onClick={() => update.mutate()}
        >
          Update
        </Button>
      }
    >
      {release.newPermissions.length
        ? `Release #${release.number} asks for new permissions, which you'll review before updating.`
        : `You're on #${host.context.plugin.release}. Update to #${release.number} for the newest checks and fixes.`}
    </Banner>
  )
}
