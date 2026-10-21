import { createFileRoute } from "@tanstack/react-router"

import { PluginPage } from "@/features/plugins/components/PluginPage"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents/plugins/$name/$")({
  component: PluginRoute,
  head: () => ({ meta: [{ title: pageTitle("Plugin") }] }),
})

function PluginRoute() {
  const { name, _splat } = Route.useParams()
  return <PluginPage name={name} path={`/${_splat ?? ""}`} />
}
