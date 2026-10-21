import { createFileRoute } from "@tanstack/react-router"

import { PageView } from "@/components/PageView"

export const Route = createFileRoute("/$owner/$repo/$")({
  component: PageRoute,
})

function PageRoute() {
  const { owner, repo, _splat } = Route.useParams()
  const repository = `${owner}/${repo}`.toLowerCase()
  const path = _splat ?? ""
  return (
    <PageView
      key={`${repository}:${path}`}
      repository={repository}
      path={path}
    />
  )
}
