import { createFileRoute } from "@tanstack/react-router"

import { AppsPage } from "@/features/apps/components/AppsPage"
import { pageTitle } from "@/lib/pageTitle"

export const Route = createFileRoute("/agents/apps")({
  component: AppsPage,
  head: () => ({ meta: [{ title: pageTitle("Apps") }] }),
})
