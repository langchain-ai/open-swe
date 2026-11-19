import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Input } from "@langchain/macaw-components/Input"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import { GitBranchIcon } from "@phosphor-icons/react/dist/ssr/GitBranch"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"
import { useState } from "react"

import { RepoSelector } from "@/features/settings/components/RepoSelector"
import {
  type Project,
  useCreateProject,
} from "@/features/projects/lib/projects"
import { hasLinkedGitHub } from "@/lib/linkedGitHub"
import { useRepos } from "@/lib/profile"
import { useSession } from "@/lib/session"

/** Names a new project and adds at most one GitHub repository, whose threads it then files. */
export function CreateProjectDialog({
  open,
  onOpenChange,
  initialName = "",
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  initialName?: string
  onCreated?: (project: Project) => void
}) {
  const session = useSession()
  const gitHub = Boolean(session.data && hasLinkedGitHub(session.data))
  const [name, setName] = useState(initialName)
  const [repo, setRepo] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const repos = useRepos()
  const createProject = useCreateProject()

  const create = async () => {
    setError(null)
    const project = await createProject
      .mutateAsync({
        name: name.trim(),
        repo_full_name: repo,
      })
      .catch((cause: unknown) => {
        setError(cause instanceof Error ? cause.message : String(cause))
        return null
      })
    if (!project) return
    onCreated?.(project)
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Create project"
        className="w-[min(36rem,calc(100vw-2rem))]"
      >
        <form
          className="flex flex-col gap-space-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (name.trim() && !createProject.isPending) void create()
          }}
        >
          <Input
            aria-label="Project name"
            autoFocus
            leftDecorator={
              <FolderIcon size={16} className="text-icon-secondary" />
            }
            placeholder="Project name"
            value={name}
            onChange={setName}
          />
          {gitHub && (
            <div className="flex flex-col gap-space-2">
              <p className="text-sm font-medium text-primary">Sources</p>
              <div className="flex flex-col gap-space-1 rounded-lg border border-subtle p-space-3">
                {repo ? (
                  <SourceRow
                    icon={GitBranchIcon}
                    label={repo}
                    detail="GitHub repository"
                    onRemove={() => setRepo(null)}
                  />
                ) : (
                  <div className="flex flex-col items-center gap-space-2 py-space-2 text-sm text-secondary">
                    <span>Add a repository from GitHub</span>
                    <RepoSelector
                      emptySelectionLabel="No repository"
                      noMatchesLabel="No matching repositories"
                      onRepoChange={setRepo}
                      placeholder="Add"
                      repos={repos.data?.repositories}
                      searchPlaceholder="Search repositories…"
                      selectedRepo={undefined}
                    />
                  </div>
                )}
              </div>
            </div>
          )}
          {error && (
            <p role="alert" className="text-sm text-error-secondary">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-space-2">
            <Button
              color="secondary"
              variant="outlined"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              color="primary"
              type="submit"
              disabled={!name.trim() || createProject.isPending}
            >
              {createProject.isPending ? "Creating…" : "Create project"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function SourceRow({
  icon: Icon,
  label,
  detail,
  onRemove,
}: {
  icon: typeof FolderIcon
  label: string
  detail: string
  onRemove: () => void
}) {
  return (
    <div className="flex items-center gap-space-2 rounded-md px-space-2 py-space-1 text-sm">
      <Icon size={16} className="shrink-0 text-icon-secondary" />
      <span className="min-w-0 flex-1 truncate">
        <span className="text-primary">{label}</span>
        <span className="text-tertiary"> {detail}</span>
      </span>
      <IconButton
        icon={XIcon}
        label={`Remove ${label}`}
        size="xs"
        color="secondary"
        variant="plain"
        onClick={onRemove}
      />
    </div>
  )
}
