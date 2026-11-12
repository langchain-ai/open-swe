import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Spinner } from "@langchain/macaw-components/Spinner"
import type { Timestamp } from "./api"

export function formatTime(value: Timestamp | null) {
  if (value === null) return "Not verified"
  const date = new Date(typeof value === "number" ? value * 1000 : value)
  if (!Number.isFinite(date.getTime())) return "Unknown time"
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

export function LoadingState() {
  return (
    <div
      role="status"
      className="flex items-center justify-center gap-space-2 py-24 text-sm text-secondary"
    >
      <Spinner size="xs" />
      Loading settings…
    </div>
  )
}

export function ErrorState({
  error,
  retry,
}: {
  error: Error
  retry: () => void
}) {
  return (
    <div role="alert">
      <Banner
        intent="error"
        title="Unable to load incident settings"
        action={
          <Button color="secondary" variant="outlined" onClick={retry}>
            Try again
          </Button>
        }
      >
        {error.message}
      </Banner>
    </div>
  )
}
