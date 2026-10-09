import { Button } from "@langchain/macaw-components/Button"
import { ErrorState } from "@langchain/macaw-components/ErrorState"
import { useEffect, useState } from "react"

export function LoadError({
  error,
  title = "Something went wrong",
  context,
  retry = () => window.location.reload(),
}: {
  error: unknown
  title?: string
  context?: string
  retry?: () => void
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
    <div role="alert" className="flex min-w-0 flex-1 overflow-y-auto p-space-5">
      <ErrorState
        title={title}
        message="Try again. If this keeps happening, share the page URL and the details below with your workspace admin."
        contentClassName="mx-auto max-w-lg pt-space-8"
        action={
          <div className="flex w-full flex-col items-center gap-space-4">
            <pre className="max-h-48 w-full overflow-auto rounded-md bg-surface-level-2 p-space-3 text-xs break-all whitespace-pre-wrap">
              {details}
            </pre>
            <div className="flex gap-space-2">
              <Button color="primary" onClick={retry}>
                Try again
              </Button>
              <Button
                color="secondary"
                variant="outlined"
                onClick={() => window.location.assign(import.meta.env.BASE_URL)}
              >
                Back to home
              </Button>
            </div>
          </div>
        }
      />
    </div>
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
