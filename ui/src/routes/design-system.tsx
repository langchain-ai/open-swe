import { createFileRoute, notFound } from "@tanstack/react-router"
import { lazy, Suspense } from "react"

const Gallery = import.meta.env.DEV
  ? lazy(() => import("@/components/design-system/DesignSystemGallery"))
  : () => null

export const Route = createFileRoute("/design-system")({
  beforeLoad: () => {
    if (!import.meta.env.DEV) throw notFound()
  },
  component: () => (
    <Suspense fallback={<p className="p-8">Loading component gallery…</p>}>
      <Gallery />
    </Suspense>
  ),
})
