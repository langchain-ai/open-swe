import type { ReactNode } from "react"

import { Button } from "@langchain/macaw-components/Button"
import { Spinner } from "@langchain/macaw-components/Spinner"

/** The shared shape of every per-pull-request action: button, label, errors. */
export function PullRequestActionButton({
  label,
  disabled,
  loading = false,
  onClick,
  errors = [],
  children,
}: {
  label: string
  disabled: boolean
  loading?: boolean
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
          aria-busy={loading}
          aria-label={label}
          aria-live="polite"
          onClick={onClick}
        >
          {loading && <Spinner size="xxs" />}
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
