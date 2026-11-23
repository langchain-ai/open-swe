import { useRef, useState } from "react"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import { Text } from "@langchain/macaw-components/Text"
import { ArrowClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowClockwise"
import { CopyErrorButton } from "@/components/CopyErrorButton"

export function RunErrorBanner({
  threadId,
  traceUrl,
  retry,
  disabled = false,
}: {
  threadId: string
  traceUrl?: string | null
  retry?: () => Promise<void>
  disabled?: boolean
}) {
  const pending = useRef(false)
  const [retrying, setRetrying] = useState(false)
  const retryRun = async () => {
    if (!retry || disabled || pending.current) return
    pending.current = true
    setRetrying(true)
    try {
      await retry()
    } catch {
      // The send mutation already reports its own failure.
    }
    pending.current = false
    setRetrying(false)
  }

  return (
    <Banner intent="error" icon={null} dismissible className="gap-space-4">
      <div className="flex flex-wrap items-center justify-between gap-x-space-4 gap-y-space-2">
        <Text as="p" variant="sm" color="secondary">
          The run failed.
        </Text>
        <div className="flex flex-wrap items-center gap-space-3">
          {retry && (
            <Button
              color="secondary"
              variant="outlined"
              size="xs"
              leftDecorator={ArrowClockwiseIcon}
              disabled={disabled || retrying}
              loading={retrying}
              onClick={() => void retryRun()}
            >
              Retry
            </Button>
          )}
          <CopyErrorButton
            error="The last run hit an error before it could finish."
            details={{ Thread: threadId, Trace: traceUrl }}
          />
          {traceUrl && (
            <Button
              as={<a href={traceUrl} target="_blank" rel="noreferrer" />}
              color="secondary"
              variant="plain"
              size="xs"
            >
              Open trace
            </Button>
          )}
        </div>
      </div>
    </Banner>
  )
}
