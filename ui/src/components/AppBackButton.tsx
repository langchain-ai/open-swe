import { ArrowLeftIcon } from "@phosphor-icons/react"
import { useRouter } from "@tanstack/react-router"
import { useSyncExternalStore } from "react"

import { cn } from "@/lib/utils"

export function AppBackButton({ className }: { className?: string }) {
  const router = useRouter()
  const canGoBack = useSyncExternalStore(
    router.history.subscribe,
    router.history.canGoBack,
    () => false
  )
  return (
    <button
      type="button"
      aria-label="Go back"
      title="Go back"
      data-no-drag=""
      disabled={!canGoBack}
      onClick={() => router.history.back()}
      className={cn(
        "flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-30",
        className
      )}
    >
      <ArrowLeftIcon className="size-4" aria-hidden />
    </button>
  )
}
