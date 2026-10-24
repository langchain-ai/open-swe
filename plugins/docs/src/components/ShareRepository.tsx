import { Button } from "@langchain/macaw-components/Button"
import { EmptyState } from "@langchain/macaw-components/EmptyState"
import { FileTextIcon } from "@phosphor-icons/react/dist/ssr/FileText"
import { useNavigate } from "@tanstack/react-router"

import { usePickRepository } from "@/lib/queries"

/** Opens a repository the person picks in the dashboard's picker. */
export function useOpenPickedRepository() {
  const pick = usePickRepository()
  const navigate = useNavigate()
  return {
    pending: pick.isPending,
    open: (suggested?: string) =>
      pick.mutate(suggested, {
        onSuccess: (picked) => {
          if (!picked) return
          const [owner = "", repo = ""] = picked.fullName.split("/")
          void navigate({ to: "/$owner/$repo", params: { owner, repo } })
        },
      }),
  }
}

/** Asks for a repository Docs has not been given: any, or the one a link named. */
export function ShareRepository({ repository }: { repository?: string }) {
  const { pending, open } = useOpenPickedRepository()
  return (
    <div className="flex h-svh items-center justify-center p-space-6">
      <EmptyState
        icon={FileTextIcon}
        title={
          repository ? `Open ${repository} in Docs` : "Choose a repository"
        }
        description={
          repository
            ? "Docs can only open repositories you share with it. Share this one to see its pages."
            : "Docs keeps its pages as Markdown in a GitHub repository. Pick one to start; Docs sees only the repositories you share."
        }
        action={
          <Button size="sm" loading={pending} onClick={() => open(repository)}>
            {repository ? `Share ${repository}` : "Choose a repository"}
          </Button>
        }
      />
    </div>
  )
}
