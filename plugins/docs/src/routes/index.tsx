import { Spinner } from "@langchain/macaw-components/Spinner"
import { Navigate, createFileRoute } from "@tanstack/react-router"

import { ShareRepository } from "@/components/ShareRepository"
import { useRepositories, useTabTitle } from "@/lib/queries"

export const Route = createFileRoute("/")({ component: Home })

function Home() {
  const repositories = useRepositories()
  useTabTitle(null)
  if (repositories.isPending)
    return (
      <div className="flex h-svh items-center justify-center">
        <Spinner />
      </div>
    )
  const first = repositories.data?.[0]
  if (first) {
    const [owner = "", repo = ""] = first.fullName.split("/")
    return <Navigate to="/$owner/$repo" params={{ owner, repo }} replace />
  }
  return <ShareRepository />
}
