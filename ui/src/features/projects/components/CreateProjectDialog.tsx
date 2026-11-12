import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { CaretDownIcon } from "@langchain/macaw-components/icons"
import { Input } from "@langchain/macaw-components/Input"
import { FolderIcon } from "@phosphor-icons/react/dist/ssr/Folder"
import { FolderPlusIcon } from "@phosphor-icons/react/dist/ssr/FolderPlus"
import { GitBranchIcon } from "@phosphor-icons/react/dist/ssr/GitBranch"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"
import { useRef, useState } from "react"

import { DropdownMenuItem } from "@/components/MenuItem"
import type { DesktopProject } from "@/desktop"
import { useLocalThreads } from "@/features/agents/lib/desktopLocal"
import { RepoSelector } from "@/features/settings/components/RepoSelector"
import {
  pickFolderForProject,
  useFolderProject,
} from "@/features/projects/lib/desktopFolders"
import {
  type Project,
  projectsApi,
  useCreateProject,
} from "@/features/projects/lib/projects"
import { hasLinkedGitHub } from "@/lib/linkedGitHub"
import { useRepos } from "@/lib/profile"
import { useSession } from "@/lib/session"

type Source = "computer" | "github"

const SOURCE_LABEL: Record<Source, string> = {
  computer: "this computer",
  github: "GitHub",
}

/**
 * Names a new project and adds its sources: folders on this Mac (desktop
 * only) and at most one GitHub repository, whose threads it then files. A
 * folder that is a clone of a GitHub repository brings that repository.
 */
export function CreateProjectDialog({
  open,
  onOpenChange,
  initialName = "",
  workspace = null,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  initialName?: string
  workspace?: string | null
  onCreated?: (project: Project) => void
}) {
  const desktop = typeof window !== "undefined" ? window.openSweDesktop : null
  const canAddFolders = Boolean(desktop?.linkProject)
  const session = useSession()
  const gitHub = Boolean(session.data && hasLinkedGitHub(session.data))
  const sources: Array<Source> = [
    ...(canAddFolders ? (["computer"] as const) : []),
    ...(gitHub ? (["github"] as const) : []),
  ]
  const [name, setName] = useState(initialName)
  const [source, setSource] = useState<Source | undefined>(sources[0])
  const [folders, setFolders] = useState<Array<DesktopProject>>([])
  const [repo, setRepo] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const claims = useRef(new Map<string, (projectId: string | null) => void>())
  const repos = useRepos()
  const localThreads = useLocalThreads()
  const folderProject = useFolderProject()
  const createProject = useCreateProject()

  // A folder dropped from the dialog, or left when it closes, gets its own project.
  const release = (folder: DesktopProject) => {
    claims.current.get(folder.cwd)?.(null)
    claims.current.delete(folder.cwd)
    void folderProject(folder, localThreads.data)
  }
  const close = () => {
    folders.forEach(release)
    setFolders([])
    onOpenChange(false)
  }

  const addFolder = async () => {
    const picked = await pickFolderForProject()
    if (!picked) return
    const { folder, settle } = picked
    setError(null)
    if (folder.repo) {
      // A clone files through its repository, not through the folder.
      if (repo && repo.toLowerCase() !== folder.repo.toLowerCase())
        setError("A project has one repository; remove the other first.")
      else setRepo(folder.repo)
      return
    }
    if (folders.some((item) => item.cwd === folder.cwd)) return
    claims.current.set(folder.cwd, settle)
    setFolders((current) => [...current, folder])
  }

  const create = async () => {
    setError(null)
    const project = await createProject
      .mutateAsync({
        name: name.trim(),
        workspace,
        repo_full_name: repo,
      })
      .catch((cause: unknown) => {
        setError(cause instanceof Error ? cause.message : String(cause))
        return null
      })
    if (!project) return
    const linked = await Promise.all(
      folders.map(async (folder) => {
        await desktop?.linkProject?.({
          cwd: folder.cwd,
          projectId: project.id,
        })
        claims.current.get(folder.cwd)?.(project.id)
        claims.current.delete(folder.cwd)
        await Promise.all(
          (localThreads.data ?? [])
            .filter((thread) => thread.cwd === folder.cwd)
            .map((thread) => projectsApi.fileThread(thread.id, project.id))
        )
      })
    ).catch((cause: unknown) => {
      setError(
        `Created ${project.name}, but couldn't add its folders: ${cause instanceof Error ? cause.message : String(cause)}`
      )
      return null
    })
    if (!linked) return
    setFolders([])
    onCreated?.(project)
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : close())}>
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
          {sources.length > 0 && (
            <div className="flex flex-col gap-space-2">
              <p className="text-sm font-medium text-primary">Sources</p>
              <div className="flex flex-col gap-space-1 rounded-lg border border-subtle p-space-3">
                {folders.map((folder) => (
                  <SourceRow
                    key={folder.cwd}
                    icon={FolderIcon}
                    label={folder.name}
                    detail={folder.cwd}
                    onRemove={() => {
                      release(folder)
                      setFolders((current) =>
                        current.filter((item) => item.cwd !== folder.cwd)
                      )
                    }}
                  />
                ))}
                {repo && (
                  <SourceRow
                    icon={GitBranchIcon}
                    label={repo}
                    detail="GitHub repository"
                    onRemove={() => setRepo(null)}
                  />
                )}
                {(sources.length > 1 || source !== "github" || !repo) && (
                  <div className="flex flex-col items-center gap-space-2 py-space-2 text-sm text-secondary">
                    <span className="flex items-center gap-space-1">
                      {source === "github"
                        ? "Add a repository from"
                        : "Add a folder on"}
                      {sources.length > 1 ? (
                        <DropdownMenu>
                          <DropdownMenuTrigger className="flex items-center gap-0.5 text-primary outline-none focus-visible:ring-2 focus-visible:ring-focus">
                            {source ? SOURCE_LABEL[source] : ""}
                            <CaretDownIcon size={12} className="opacity-70" />
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="center" className="w-40">
                            {sources.map((option) => (
                              <DropdownMenuItem
                                key={option}
                                onSelect={() => setSource(option)}
                              >
                                {SOURCE_LABEL[option]}
                              </DropdownMenuItem>
                            ))}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      ) : (
                        <span className="text-primary">
                          {source ? SOURCE_LABEL[source] : ""}
                        </span>
                      )}
                    </span>
                    {source === "computer" && (
                      <Button
                        color="secondary"
                        variant="plain"
                        size="sm"
                        onClick={() => void addFolder()}
                      >
                        <FolderPlusIcon size={14} className="shrink-0" />
                        Add
                      </Button>
                    )}
                    {source === "github" &&
                      (repo ? (
                        <span className="text-xs text-tertiary">
                          A project has one repository
                        </span>
                      ) : (
                        <RepoSelector
                          emptySelectionLabel="No repository"
                          noMatchesLabel="No matching repositories"
                          onRepoChange={setRepo}
                          placeholder="Add"
                          repos={repos.data?.repositories}
                          searchPlaceholder="Search repositories…"
                          selectedRepo={undefined}
                        />
                      ))}
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
            <Button color="secondary" variant="outlined" onClick={close}>
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
