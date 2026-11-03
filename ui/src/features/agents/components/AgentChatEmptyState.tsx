import { LogoMark } from "@/components/Branding"

export function AgentChatEmptyState({ projectName }: { projectName?: string }) {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-space-3 px-space-4 pb-space-6 text-left">
      {!projectName && <LogoMark alt="" className="mb-space-2 size-9" />}
      <h1 className="font-heading text-2xl font-medium tracking-tight sm:text-3xl">
        {projectName ? "Start a new chat" : "What would you like to work on?"}
      </h1>
      <p className="max-w-lg text-sm leading-relaxed text-secondary">
        Explore an idea, find an answer, or turn a plan into something real.
      </p>
    </div>
  )
}
