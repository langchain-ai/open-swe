import { LogoMark } from "@/components/Branding"

export function AgentChatEmptyState() {
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
