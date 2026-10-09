import { toast } from "sonner"

import { api } from "@/lib/api"
import { WebRequestError } from "@/lib/web-fetch"
import { getDatadogRum } from "@/lib/datadog"

export interface ErrorReport {
  title: string
  error: unknown
  mutation?: string
  /** False when the caller already shows the failure inline. */
  showToast?: boolean
}

/** Field caps enforced by `ClientErrorReport` in openswe/web/client_errors.py. */
const REPORT_LIMITS = {
  title: 200,
  error_message: 2000,
  mutation: 500,
  path: 500,
} as const

function errorId(error: unknown): string {
  if (error instanceof WebRequestError && error.requestId)
    return error.requestId
  return `err_${crypto.randomUUID()}`
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
    <span className="flex flex-col gap-1">
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
      <span className="font-mono text-[11px] opacity-70">ID {id}</span>
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
  const id = errorId(error)
  const message = errorMessage(error)
  const status = error instanceof WebRequestError ? error.status : null

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

  getDatadogRum()?.addError?.(error, {
    error_id: id,
    error_title: title,
    mutation: mutation ?? null,
    status,
  })

  api
    .reportClientError({
      error_id: id,
      title: title.slice(0, REPORT_LIMITS.title),
      error_message: message.slice(0, REPORT_LIMITS.error_message),
      status,
      mutation: mutation?.slice(0, REPORT_LIMITS.mutation) ?? null,
      path: (typeof window === "undefined"
        ? ""
        : window.location.pathname
      ).slice(0, REPORT_LIMITS.path),
    })
    .catch((reportFailure: unknown) => {
      console.warn("Could not report client error", reportFailure)
    })
  return id
}
