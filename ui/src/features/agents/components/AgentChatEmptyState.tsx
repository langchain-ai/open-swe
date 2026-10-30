import { LogoMark } from "@/components/Branding"
import { useAgentWorkspace } from "@/features/agents/lib/agentWorkspace"

export function AgentChatEmptyState() {
  const agentWorkspace = useAgentWorkspace()
  if (agentWorkspace)
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-space-3 px-space-4 pb-space-6 text-left">
        <LogoMark alt="" className="mb-space-2 size-9" />
        <h1 className="font-heading text-2xl font-medium tracking-tight sm:text-3xl">
          What would you like to work on?
        </h1>
        <p className="max-w-lg text-sm leading-relaxed text-secondary">
          Explore an idea, find an answer, or turn a plan into something real.
        </p>
      </div>
    )
  return (
    <div className="flex min-h-0 flex-1 overflow-y-auto px-space-3 py-space-5 sm:px-space-5 sm:py-space-6">
      <div className="mx-auto flex min-h-full w-full max-w-3xl flex-1 flex-col items-center justify-center gap-space-5">
        <LogoMark
          alt=""
          className="size-14 opacity-30 grayscale dark:opacity-20"
        />
        <h1 className="text-center text-2xl tracking-tight sm:text-3xl">
          What should we build?
        </h1>
      </div>
    </div>
  )
}
