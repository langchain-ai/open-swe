import { createFileRoute } from "@tanstack/react-router"

import { LoadError } from "@/components/LoadError"
import { AppPreviewPage } from "@/features/agents/components/AppPreviewPage"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents/$threadId_/preview/$port")({
  component: AppPreviewRoute,
  head: () => ({ meta: [{ title: pageTitle("App preview") }] }),
})

function AppPreviewRoute() {
  const { threadId, port } = Route.useParams()
  const portNumber = /^\d{1,5}$/.test(port) ? Number(port) : NaN
  if (!(portNumber >= 1 && portNumber <= 65535))
    return (
      <LoadError
        title="Unable to open the app"
        error={`"${port}" is not a port number.`}
      />
    )
  return <AppPreviewPage threadId={threadId} port={portNumber} />
}
