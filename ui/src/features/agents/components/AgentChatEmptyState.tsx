import type { ReactNode } from "react"

import { LogoMark } from "@/components/Branding"

export function AgentChatEmptyState(props: { logo?: ReactNode }) {
  return (
    <div className="mx-auto w-full max-w-3xl px-space-4 pb-space-6">
      <div className="flex items-center justify-center gap-space-3">
        {props.logo ?? <LogoMark alt="" className="size-8 shrink-0" />}
        <h1 className="cursor-default text-center font-heading text-2xl tracking-tight select-none sm:text-3xl">
          What should we work on?
        </h1>
      </div>
    </div>
  )
}
