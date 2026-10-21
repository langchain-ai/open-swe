import { createFileRoute } from "@tanstack/react-router"

import { pageTitle } from "@/lib/pageTitle"

// The agents layout's PluginTabs draws the plugin, so it stays mounted while
// the person is elsewhere; this route only says which one is on screen.
export const Route = createFileRoute("/agents/plugins/$name/$")({
  component: () => null,
  head: () => ({ meta: [{ title: pageTitle("Plugin") }] }),
})
