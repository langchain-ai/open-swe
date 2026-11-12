import { Button } from "@langchain/macaw-components/Button"
import { useCopy } from "@langchain/macaw-components/CopyButton"
import { CheckIcon } from "@phosphor-icons/react/dist/ssr/Check"
import { CopySimpleIcon } from "@phosphor-icons/react/dist/ssr/CopySimple"

/** Copies a failure's error text with the IDs and links needed to debug it. */
export function CopyErrorButton({
  error,
  details = {},
  className,
}: {
  error: string
  details?: Record<string, string | null | undefined>
  className?: string
}) {
  const context = Object.entries(details)
    .filter(([, value]) => value)
    .map(([key, value]) => `${key}: ${value}`)
  const { copied, onCopy } = useCopy({
    copy: [error, ...(context.length ? ["", ...context] : [])].join("\n"),
  })
  return (
    <Button
      size="xs"
      color="secondary"
      variant="plain"
      leftDecorator={copied ? CheckIcon : CopySimpleIcon}
      onClick={(event) => {
        event.stopPropagation()
        onCopy()
      }}
      className={className}
    >
      {copied ? "Copied" : "Copy debugging info"}
    </Button>
  )
}
