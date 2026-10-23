import { ErrorState } from "@langchain/macaw-components/ErrorState"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { Outlet, createFileRoute } from "@tanstack/react-router"

import { ShareRepository } from "@/components/ShareRepository"
import { Sidebar } from "@/components/Sidebar"
import { OpenPage, useAgentTools } from "@/lib/agent"
import type { Draft } from "@/lib/model"
import {
  useDrafts,
  useRepositories,
  useRepositoryWatch,
  useSpace,
} from "@/lib/queries"

export const Route = createFileRoute("/$owner/$repo")({
  component: SpaceLayout,
})

function SpaceLayout() {
  const { owner, repo } = Route.useParams()
  const repository = `${owner}/${repo}`.toLowerCase()
  const shared = useRepositories()
  if (shared.isPending)
    return (
      <div className="flex h-svh items-center justify-center">
        <Spinner />
      </div>
    )
  // A link someone shared can name a repository this person has not shared yet.
  if (!shared.data?.some((one) => one.fullName === repository))
    return <ShareRepository repository={repository} />
  return <Space key={repository} repository={repository} />
}

function Space({ repository }: { repository: string }) {
  useRepositoryWatch(repository)
  const space = useSpace(repository)
  const drafts = useDrafts(repository)
  if (space.error || drafts.error)
    return (
      <div className="flex h-svh items-center justify-center p-space-6">
        <ErrorState
          title={`Couldn't open ${repository}`}
          message={(space.error ?? drafts.error)?.message}
          action={null}
        />
      </div>
    )
  if (!space.data || !drafts.data)
    return (
      <div className="flex h-svh items-center justify-center">
        <Spinner />
      </div>
    )
  return (
    <Loaded
      repository={repository}
      pages={space.data.pages}
      drafts={drafts.data}
      truncated={space.data.truncated}
    />
  )
}

function Loaded({
  repository,
  pages,
  drafts,
  truncated,
}: {
  repository: string
  pages: string[]
  drafts: Map<string, Draft>
  truncated: boolean
}) {
  const openPage = useAgentTools(repository, pages, drafts)
  return (
    <OpenPage.Provider value={openPage}>
      <div className="flex h-svh min-h-0">
        <Sidebar
          repository={repository}
          pages={pages}
          drafts={drafts}
          truncated={truncated}
        />
        <main className="flex min-w-0 flex-1 flex-col">
          <Outlet />
        </main>
      </div>
    </OpenPage.Provider>
  )
}
