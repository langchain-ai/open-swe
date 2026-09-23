import { toast } from "sonner"

import { api } from "@/lib/api"
import { DashboardRequestError } from "@/lib/dashboard-fetch"
import { getDatadogRum } from "@/lib/datadog"

export interface ErrorReport {
  title: string
  error: unknown
  mutation?: string
}

function errorId(error: unknown): string {
  if (error instanceof DashboardRequestError && error.requestId)
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
      <span>{message}</span>
      <span className="font-mono text-[11px] opacity-70">ID {id}</span>
    </span>
  )
}

/** Shows a failed action to the user and records it in Datadog under one searchable ID. */
export function reportError({ title, error, mutation }: ErrorReport): string {
  const id = errorId(error)
  const message = errorMessage(error)
  const status = error instanceof DashboardRequestError ? error.status : null

  toast.error(title, {
    id,
    description: <ErrorToastBody message={message} id={id} />,
    duration: 10_000,
    action: {
      label: "Copy ID",
      onClick: () => {
        void navigator.clipboard?.writeText(id).catch((copyError: unknown) => {
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
      title,
      error_message: message,
      status,
      mutation: mutation ?? null,
      path: typeof window === "undefined" ? "" : window.location.pathname,
    })
    .catch((reportFailure: unknown) => {
      console.warn("Could not report client error", reportFailure)
    })
  return id
}
