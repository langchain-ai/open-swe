import { LogoMark } from "@/components/Branding"

export function AgentChatEmptyState() {
  return (
    <div className="mx-auto w-full max-w-3xl px-space-4 pb-space-6">
      <h1 className="flex items-center justify-center gap-space-3 text-center font-heading text-2xl tracking-tight sm:text-3xl">
        <LogoMark alt="" className="size-8 shrink-0" />
        What should we work on?
      </h1>
    </div>
  )
}
