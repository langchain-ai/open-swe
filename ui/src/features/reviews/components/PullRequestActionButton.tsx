import type { ReactNode } from "react"

import { Inline, Stack } from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Icon } from "@langchain/gtm-platform-design-system/ui/icon"

import { AlertTriangle } from "@/components/glyphs"

/** Failures a per-pull-request action reports under its control. */
export function ActionErrors({ errors }: { errors: Array<Error | null> }) {
  return errors.map(
    (error, index) =>
      error && (
        <Inline
          key={index}
          role="alert"
          gap="xs"
          align="start"
          className="text-label text-risk"
        >
          <Icon icon={AlertTriangle} size="sm" className="mt-0.5" />
          <span>{error.message}</span>
        </Inline>
      )
  )
}

/** The shared shape of every per-pull-request action: button, label, errors. */
export function PullRequestActionButton({
  label,
  disabled,
  onClick,
  errors = [],
  children,
}: {
  label: string
  disabled: boolean
  onClick: () => void
  errors?: Array<Error | null>
  children?: ReactNode
}) {
  return (
    <Stack gap="xs">
      <Inline gap="sm" wrap>
        {children}
        <Button
          size="compact"
          variant="outline"
          disabled={disabled}
          aria-live="polite"
          onClick={onClick}
        >
          {label}
        </Button>
      </Inline>
      <ActionErrors errors={errors} />
    </Stack>
  )
}
