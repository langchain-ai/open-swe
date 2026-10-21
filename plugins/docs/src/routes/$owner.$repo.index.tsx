import { Text } from "@langchain/macaw-components/Text"
import { Link, createFileRoute } from "@tanstack/react-router"

import { isHidden, pageTitle } from "@/lib/model"
import { useDrafts, useSpace } from "@/lib/queries"

export const Route = createFileRoute("/$owner/$repo/")({ component: SpaceHome })

function SpaceHome() {
  const { owner, repo } = Route.useParams()
  const repository = `${owner}/${repo}`.toLowerCase()
  const space = useSpace(repository).data
  const drafts = [...(useDrafts(repository).data ?? new Map()).entries()]
  const pages = (space?.pages ?? []).filter((path) => !isHidden(path))
  return (
    <div className="mx-auto w-full max-w-3xl overflow-y-auto px-space-6 py-space-9">
      <Text as="h1" variant="h2" weight="semibold">
        {repository}
      </Text>
      <p className="mt-space-2 text-sm text-secondary">
        {pages.length} pages on {space?.defaultBranch}. Edits happen together in
        drafts, and each version is published as a pull request.
      </p>
      <Text as="h2" variant="h5" weight="medium" className="mt-space-8">
        Drafts in progress
      </Text>
      {drafts.length === 0 ? (
        <p className="mt-space-2 text-sm text-secondary">
          None yet. Open a page and choose Edit to start one.
        </p>
      ) : (
        <ul className="mt-space-3 flex flex-col gap-space-2">
          {drafts.map(([path, draft]) => (
            <li key={path} className="flex flex-col">
              <Link
                to="/$owner/$repo/$"
                params={{ owner, repo, _splat: path }}
                className="text-sm font-medium text-primary capitalize hover:underline"
              >
                {pageTitle(path)}
              </Link>
              <span className="text-xs text-secondary">
                {path} · started by {draft.startedBy}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
