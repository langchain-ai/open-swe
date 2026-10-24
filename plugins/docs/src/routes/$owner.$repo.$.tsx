import { createFileRoute } from "@tanstack/react-router"

import { PageView } from "@/components/PageView"
import { pageTitle } from "@/lib/model"
import { useTabTitle } from "@/lib/queries"

export const Route = createFileRoute("/$owner/$repo/$")({
  component: PageRoute,
})

function PageRoute() {
  const { owner, repo, _splat } = Route.useParams()
  const repository = `${owner}/${repo}`.toLowerCase()
  const path = _splat ?? ""
  const title = pageTitle(path)
  useTabTitle(title.charAt(0).toUpperCase() + title.slice(1))
  return (
    <PageView
      key={`${repository}:${path}`}
      repository={repository}
      path={path}
    />
  )
}
