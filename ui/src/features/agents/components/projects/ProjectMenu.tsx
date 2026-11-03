import { useState } from "react"
import { useNavigate } from "@tanstack/react-router"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Dialog, DialogContent } from "@langchain/macaw-components/Dialog"
import { Button } from "@langchain/macaw-components/Button"
import { DotsThreeIcon } from "@phosphor-icons/react/dist/ssr/DotsThree"
import { DropdownMenuItem } from "@/components/MenuItem"
import {
  useDeleteChatProject,
  type ChatProject,
} from "@/features/agents/lib/chatProjects"
import { ProjectDialog } from "./ProjectDialog"

export function ProjectMenu({
  project,
  className,
}: {
  project: ChatProject
  className?: string
}) {
  const [dialog, setDialog] = useState<"rename" | "settings" | "remove" | null>(
    null
  )
  const remove = useDeleteChatProject()
  const navigate = useNavigate()
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <IconButton
            label={`Actions for project ${project.name}`}
            icon={DotsThreeIcon}
            size="xs"
            color="secondary"
            variant="plain"
            disabled={remove.isPending}
            className={className}
          />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-44">
          <DropdownMenuItem onSelect={() => setDialog("rename")}>
            Rename
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setDialog("settings")}>
            Settings
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            className="text-error"
            onSelect={() => setDialog("remove")}
          >
            Remove
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ProjectDialog
        project={project}
        open={dialog === "rename" || dialog === "settings"}
        renameOnly={dialog === "rename"}
        onOpenChange={(open) => {
          if (!open) setDialog(null)
        }}
      />
      <Dialog
        open={dialog === "remove"}
        onOpenChange={(open) => {
          if (!open && !remove.isPending) setDialog(null)
        }}
      >
        <DialogContent
          title="Remove project"
          description={`Remove “${project.name}”? Its chats keep their pinned or archived status. Other active chats remain in Recents. No chats, folders, or files will be deleted.`}
        >
          <div className="flex justify-end gap-space-2">
            <Button
              size="sm"
              color="secondary"
              variant="outlined"
              disabled={remove.isPending}
              onClick={() => setDialog(null)}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              color="error"
              disabled={remove.isPending}
              onClick={() =>
                remove.mutate(project.id, {
                  onSuccess: () => {
                    setDialog(null)
                    if (
                      new URLSearchParams(window.location.search).get(
                        "project"
                      ) === project.id
                    )
                      void navigate({ to: "/agents" })
                  },
                })
              }
            >
              {remove.isPending ? "Removing…" : "Remove project"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
