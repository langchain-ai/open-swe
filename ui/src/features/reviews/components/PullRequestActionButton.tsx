import type { ReactNode } from "react"

import { Button } from "@langchain/macaw-components/Button"

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
    <div>
      <div className="flex flex-wrap items-center gap-space-2">
        {children}
        <Button
          size="xs"
          color="secondary"
          variant="outlined"
          disabled={disabled}
          aria-live="polite"
          onClick={onClick}
        >
          {label}
        </Button>
      </div>
      {errors.map(
        (error, index) =>
          error && (
            <p
              key={index}
              role="alert"
              className="mt-space-1 text-error-secondary"
            >
              {error.message}
            </p>
          )
      )}
    </div>
  )
}
