import { Button } from "@langchain/macaw-components/Button"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Input } from "@langchain/macaw-components/Input"
import { DotsThreeIcon } from "@phosphor-icons/react/dist/ssr/DotsThree"
import { GearSixIcon } from "@phosphor-icons/react/dist/ssr/GearSix"
import { SignOutIcon } from "@phosphor-icons/react/dist/ssr/SignOut"
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash"
import { useState } from "react"

import { DropdownMenuItem } from "@/components/MenuItem"
import { ConfirmDialog } from "@/features/settings/components/ConfirmDialog"
import {
  type Project,
  type ProjectMember,
  useDeleteProject,
  useRemoveProjectMember,
  useSetProjectMember,
  useUpdateProject,
} from "@/features/projects/lib/projects"
import { useSession } from "@/lib/session"

/** The overflow menu on a project's sidebar folder. */
export function ProjectFolderMenu({ project }: { project: Project }) {
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [confirming, setConfirming] = useState<"delete" | "leave" | null>(null)
  const session = useSession()
  const deleteProject = useDeleteProject()
  const removeMember = useRemoveProjectMember()
  const owner = project.role === "owner"
  const viewerId = session.data?.user_id ?? ""

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <IconButton
            icon={DotsThreeIcon}
            label={`${project.name} options`}
            size="xs"
            color="secondary"
            variant="plain"
            className="hidden group-hover/folder:inline-flex data-[state=open]:inline-flex"
          />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-48">
          <DropdownMenuItem onSelect={() => setSettingsOpen(true)}>
            <GearSixIcon size={14} weight="regular" className="shrink-0" />
            {owner ? "Project settings…" : "Members…"}
          </DropdownMenuItem>
          {owner ? (
            <DropdownMenuItem onSelect={() => setConfirming("delete")}>
              <TrashIcon size={14} weight="regular" className="shrink-0" />
              Delete project
            </DropdownMenuItem>
          ) : (
            <DropdownMenuItem onSelect={() => setConfirming("leave")}>
              <SignOutIcon size={14} weight="regular" className="shrink-0" />
              Leave project
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      {settingsOpen && (
        <ProjectSettingsDialog
          project={project}
          viewerId={viewerId}
          onOpenChange={setSettingsOpen}
        />
      )}
      <ConfirmDialog
        open={confirming !== null}
        onOpenChange={(open) => setConfirming(open ? confirming : null)}
        title={
          confirming === "delete"
            ? `Delete ${project.name}?`
            : `Leave ${project.name}?`
        }
        description={
          confirming === "delete"
            ? "Its threads stay, filed under their repository or under No project. Members lose the folder."
            : "Its threads you can read stay, filed under their repository or under No project."
        }
        confirmLabel={confirming === "delete" ? "Delete" : "Leave"}
        destructive
        onConfirm={() => {
          if (confirming === "delete") deleteProject.mutate(project.id)
          else
            removeMember.mutate({
              projectId: project.id,
              userId: viewerId,
              leaving: true,
            })
          setConfirming(null)
        }}
      />
    </>
  )
}

function ProjectSettingsDialog({
  project,
  viewerId,
  onOpenChange,
}: {
  project: Project
  viewerId: string
  onOpenChange: (open: boolean) => void
}) {
  const owner = project.role === "owner"
  const [name, setName] = useState(project.name)
  const [repo, setRepo] = useState(project.repo_full_name ?? "")
  const [person, setPerson] = useState("")
  const updateProject = useUpdateProject()
  const setMember = useSetProjectMember()
  const removeMember = useRemoveProjectMember()
  const nameChanged = name.trim() && name.trim() !== project.name
  const repoChanged = repo.trim() !== (project.repo_full_name ?? "")

  const save = () => {
    if (nameChanged || repoChanged)
      updateProject.mutate({
        projectId: project.id,
        changes: {
          ...(nameChanged ? { name: name.trim() } : {}),
          ...(repoChanged ? { repo_full_name: repo.trim() } : {}),
        },
      })
    onOpenChange(false)
  }
  const share = () => {
    if (!person.trim()) return
    setMember.mutate(
      { projectId: project.id, person: person.trim(), role: "member" },
      { onSuccess: () => setPerson("") }
    )
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent
        title={owner ? "Project settings" : project.name}
        description="Sharing a project shows teammates its folder; each thread stays as visible as it already was."
        className="w-[min(32rem,calc(100vw-2rem))]"
      >
        {owner && (
          <div className="flex flex-col gap-space-3">
            <Input label="Name" value={name} onChange={setName} size="sm" />
            <Input
              label="Repository"
              hintText="owner/name. Every thread in it is filed under this project."
              placeholder="No repository"
              value={repo}
              onChange={setRepo}
              size="sm"
            />
          </div>
        )}
        <div className="flex flex-col gap-space-1">
          <p className="text-sm font-medium text-primary">Members</p>
          {project.members.map((member) => (
            <MemberRow
              key={member.user_id}
              member={member}
              editable={owner && member.user_id !== viewerId}
              onRole={(role) =>
                setMember.mutate({
                  projectId: project.id,
                  person: member.login || member.user_id,
                  role,
                })
              }
              onRemove={() =>
                removeMember.mutate({
                  projectId: project.id,
                  userId: member.user_id,
                  leaving: false,
                })
              }
            />
          ))}
        </div>
        {owner && (
          <div className="flex items-end gap-space-2">
            <div className="min-w-0 flex-1">
              <Input
                label="Share with"
                placeholder="GitHub username or email"
                value={person}
                onChange={setPerson}
                size="sm"
              />
            </div>
            <Button
              color="secondary"
              variant="outlined"
              size="sm"
              disabled={!person.trim() || setMember.isPending}
              onClick={share}
            >
              Share
            </Button>
          </div>
        )}
        <div className="flex justify-end gap-space-2">
          <Button
            color="secondary"
            variant="outlined"
            onClick={() => onOpenChange(false)}
          >
            {owner ? "Cancel" : "Close"}
          </Button>
          {owner && (
            <Button
              color="primary"
              disabled={!nameChanged && !repoChanged}
              onClick={save}
            >
              Save
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

function MemberRow({
  member,
  editable,
  onRole,
  onRemove,
}: {
  member: ProjectMember
  editable: boolean
  onRole: (role: ProjectMember["role"]) => void
  onRemove: () => void
}) {
  return (
    <div className="flex items-center gap-space-2 py-space-1 text-sm">
      <span className="min-w-0 flex-1 truncate text-primary">
        {member.name}
        {member.login && member.login !== member.name && (
          <span className="text-tertiary"> @{member.login}</span>
        )}
      </span>
      <span className="shrink-0 text-xs text-secondary">
        {member.role === "owner" ? "Owner" : "Member"}
      </span>
      {editable && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <IconButton
              icon={DotsThreeIcon}
              label={`Change ${member.name}'s access`}
              size="xs"
              color="secondary"
              variant="plain"
            />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-40">
            <DropdownMenuItem
              onSelect={() =>
                onRole(member.role === "owner" ? "member" : "owner")
              }
            >
              {member.role === "owner" ? "Make member" : "Make owner"}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={onRemove}>Remove</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  )
}
