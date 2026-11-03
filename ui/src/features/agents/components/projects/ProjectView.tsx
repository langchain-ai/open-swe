import type { ReactNode } from "react"
import type { ChatProject } from "@/features/agents/lib/chatProjects"
import { ProjectMenu } from "./ProjectMenu"
import { ProjectThreads } from "./ProjectThreads"

export function ProjectView({
  project,
  children,
}: {
  project: ChatProject
  children?: ReactNode
}) {
  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-space-4 px-space-5 py-space-5">
        <header className="flex min-w-0 items-start gap-space-3">
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-xl font-medium text-primary">
              {project.name}
            </h1>
            <p className="mt-space-1 text-xs text-secondary">
              Personal project
              {project.repo
                ? ` · ${project.repo}`
                : project.localFolder
                  ? ` · ${project.localFolder.path}`
                  : ""}
            </p>
          </div>
          <ProjectMenu project={project} />
        </header>
        {children}
        <section aria-label="Project chats" className="mt-space-4">
          <h2 className="mb-space-2 px-space-2 text-xs font-medium text-secondary">
            Chats
          </h2>
          <ProjectThreads projectId={project.id} />
        </section>
      </div>
    </div>
  )
}
