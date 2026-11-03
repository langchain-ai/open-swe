import { useState } from "react"
import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import { Input } from "@langchain/macaw-components/Input"
import { RepoSelector } from "@/features/settings/components/RepoSelector"
import { useDesktopProjects } from "@/features/agents/lib/desktopProjects"
import {
  desktopDeviceId,
  useSaveChatProject,
  type ChatProject,
} from "@/features/agents/lib/chatProjects"
import { useRepos } from "@/lib/profile"
import { toast } from "sonner"

export function ProjectDialog({
  project,
  open,
  onOpenChange,
  renameOnly = false,
}: {
  project?: ChatProject
  open: boolean
  onOpenChange: (open: boolean) => void
  renameOnly?: boolean
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={
          project
            ? renameOnly
              ? "Rename project"
              : "Project settings"
            : "New project"
        }
        description="Projects organize your chats. They are personal, with no shared access or memory."
        className="w-[min(30rem,calc(100vw-2rem))]"
      >
        <ProjectForm
          key={`${project?.id ?? "new"}:${open}`}
          project={project}
          renameOnly={renameOnly}
          onClose={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  )
}

function ProjectForm({
  project,
  renameOnly,
  onClose,
}: {
  project?: ChatProject
  renameOnly: boolean
  onClose: () => void
}) {
  const [name, setName] = useState(project?.name ?? "")
  const [repo, setRepo] = useState(project?.repo ?? null)
  const [localFolder, setLocalFolder] = useState(project?.localFolder ?? null)
  const save = useSaveChatProject()
  const repos = useRepos()
  const desktop = useDesktopProjects()
  const [picking, setPicking] = useState(false)
  const selectFolder = async () => {
    setPicking(true)
    try {
      const folder = await desktop.addProject()
      if (folder) {
        setLocalFolder({ path: folder.cwd, deviceId: desktopDeviceId() })
        setRepo(null)
      }
    } catch {
      toast.error("Could not open the folder picker")
    }
    setPicking(false)
  }
  const busy = save.isPending || picking
  return (
    <form
      className="flex flex-col gap-space-4"
      onSubmit={(event) => {
        event.preventDefault()
        if (!name.trim() || busy) return
        save.mutate(
          {
            ...(project ? { id: project.id } : {}),
            name: name.trim(),
            repo,
            localFolder,
          },
          { onSuccess: onClose }
        )
      }}
    >
      <Input
        label="Name"
        size="md"
        value={name}
        onChange={setName}
        debounceMs={0}
        autoFocus
        required
        disabled={busy}
        placeholder="Project name"
      />
      {!renameOnly && (
        <>
          <div className="flex flex-col gap-space-2">
            <span className="text-sm text-secondary">
              Default Context (Optional)
            </span>
            <RepoSelector
              repos={repos.data?.repositories}
              selectedRepo={repo}
              onRepoChange={(next) => {
                setRepo(next)
                if (next) setLocalFolder(null)
              }}
              disabled={busy}
            />
            {repos.isError && (
              <p role="alert" className="text-error text-xs">
                Repositories could not be loaded. You can still create a project
                without one.
              </p>
            )}
            {window.openSweDesktop && (
              <>
                <Button
                  size="sm"
                  color="secondary"
                  variant="outlined"
                  disabled={busy}
                  onClick={() => void selectFolder()}
                >
                  Choose This Mac folder…
                </Button>
                {desktop.projects
                  .filter((folder) => folder.cwd !== localFolder?.path)
                  .map((folder) => (
                    <Button
                      key={folder.cwd}
                      size="sm"
                      color="secondary"
                      variant="plain"
                      className="justify-start truncate"
                      disabled={busy}
                      onClick={() => {
                        setLocalFolder({
                          path: folder.cwd,
                          deviceId: desktopDeviceId(),
                        })
                        setRepo(null)
                      }}
                    >
                      {folder.name}
                    </Button>
                  ))}
              </>
            )}
            {localFolder && (
              <div className="flex min-w-0 items-center gap-space-2">
                <span className="min-w-0 flex-1 text-xs break-all text-secondary">
                  This Mac: {localFolder.path}
                  {localFolder.deviceId !== desktopDeviceId()
                    ? " (on another device)"
                    : ""}
                </span>
                <Button
                  size="xs"
                  color="secondary"
                  variant="plain"
                  disabled={busy}
                  onClick={() => setLocalFolder(null)}
                >
                  Clear
                </Button>
              </div>
            )}
          </div>
          <p className="text-xs text-secondary">
            A folder is working context, not a security boundary. The agent may
            access files outside it when permitted.
          </p>
        </>
      )}
      <div className="flex justify-end gap-space-2">
        <Button
          size="sm"
          color="secondary"
          variant="outlined"
          disabled={busy}
          onClick={onClose}
        >
          Cancel
        </Button>
        <Button size="sm" type="submit" disabled={!name.trim() || busy}>
          {save.isPending ? "Saving…" : project ? "Save" : "Create project"}
        </Button>
      </div>
    </form>
  )
}
