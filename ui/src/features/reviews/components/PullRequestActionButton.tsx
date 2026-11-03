import type { ReactNode } from "react"

import { Button } from "@langchain/macaw-components/Button"
import type { IconComponent } from "@langchain/macaw-components/Icon"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { ArrowUpRightIcon } from "@phosphor-icons/react/dist/ssr/ArrowUpRight"

/** The shared shape of every per-pull-request action: button, label, errors. */
export function PullRequestActionButton({
  label,
  disabled,
  loading = false,
  onClick,
  href,
  icon,
  errors = [],
  children,
}: {
  label: string
  disabled: boolean
  loading?: boolean
  onClick?: () => void
  /** Renders the action as a link that opens this URL in a new tab. */
  href?: string
  icon?: IconComponent
  errors?: Array<Error | null>
  children?: ReactNode
}) {
  return (
    <div>
      <div className="flex flex-wrap items-center gap-space-2">
        {children}
        <Button
          as={
            href ? (
              <a href={href} target="_blank" rel="noopener noreferrer" />
            ) : undefined
          }
          size="xs"
          color="secondary"
          variant="outlined"
          leftDecorator={icon}
          rightDecorator={href ? ArrowUpRightIcon : undefined}
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
