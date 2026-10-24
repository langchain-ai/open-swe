import { toast } from "sonner"

import { recordClientError } from "@/lib/clientErrors"

export interface ErrorReport {
  title: string
  error: unknown
  mutation?: string
  /** False when the caller already shows the failure inline. */
  showToast?: boolean
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message
  return String(error)
}

export function ErrorToastBody({
  message,
  id,
}: {
  message: string
  id: string
}) {
  return (
    <span className="flex flex-col gap-space-1">
      <span>
        {message
          .split(/(https:\/\/[\w-]+\.slack\.com\/archives\/[\w/]+)/)
          .map((part, index) =>
            /^https:\/\/[\w-]+\.slack\.com\/archives\/[\w/]+$/.test(part) ? (
              <a
                key={index}
                href={part}
                target="_blank"
                rel="noopener noreferrer"
                className="underline"
              >
                Open review card in Slack
              </a>
            ) : (
              part
            )
          )}
      </span>
      <span className="font-mono text-xxs opacity-70">ID {id}</span>
    </span>
  )
}

/** Shows a failed action to the user and records it in Datadog under one searchable ID. */
export function reportError({
  title,
  error,
  mutation,
  showToast = true,
}: ErrorReport): string {
  const id = recordClientError({ title, error, mutation })
  const message = errorMessage(error)

  if (showToast)
    toast.error(title, {
      id,
      description: <ErrorToastBody message={message} id={id} />,
      duration: 10_000,
      action: {
        label: "Copy ID",
        onClick: () => {
          void navigator.clipboard
            ?.writeText(id)
            .catch((copyError: unknown) => {
              console.warn("Could not copy error ID", copyError)
            })
        },
      },
    })

  return id
}
