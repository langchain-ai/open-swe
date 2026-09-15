import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"

export function LoadError({
  error,
  title = "Something went wrong",
  context,
}: {
  error: unknown
  title?: string
  context?: string
}) {
  const details = [
    context,
    error instanceof Error ? `${error.name}: ${error.message}` : String(error),
  ]
    .filter(Boolean)
    .join("\n")

  useEffect(() => {
    console.error("UI load failed", { context, error })
  }, [context, error])

  return (
    <main
      role="alert"
      className="flex min-w-0 flex-1 items-center justify-center p-6"
    >
      <div className="w-full max-w-lg space-y-4 rounded-xl border bg-card p-6">
        <h1 className="text-lg font-semibold">{title}</h1>
        <p className="text-sm text-muted-foreground">
          Try again. If this keeps happening, share the page URL and the details
          below with your workspace admin.
        </p>
        <pre className="max-h-48 overflow-auto rounded-md bg-muted p-3 text-xs break-all whitespace-pre-wrap">
          {details}
        </pre>
        <div className="flex gap-2">
          <Button onClick={() => window.location.reload()}>Try again</Button>
          <Button
            variant="outline"
            onClick={() =>
              window.location.assign(import.meta.env.BASE_URL + "agents")
            }
          >
            Back to threads
          </Button>
        </div>
      </div>
    </main>
  )
}

export function useLoadTimedOut(loading: boolean) {
  const [timedOut, setTimedOut] = useState(false)
  useEffect(() => {
    const timer = window.setTimeout(
      () => setTimedOut(loading),
      loading ? 30_000 : 0
    )
    return () => window.clearTimeout(timer)
  }, [loading])
  return loading && timedOut
}
