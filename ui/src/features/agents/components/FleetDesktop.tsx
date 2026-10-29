import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@langchain/macaw-components/Button"
import { Input } from "@langchain/macaw-components/Input"
import { FleetChat } from "@/features/agents/components/FleetChat"
import { fleetWorkspaces } from "@/features/agents/lib/fleetChat"

export function FleetDesktop({
  workspaceId,
  threadId,
  onSelect,
}: {
  workspaceId?: string
  threadId?: string
  onSelect: (workspace?: string, thread?: string) => void
}) {
  const cache = useQueryClient()
  const connection = useQuery({
    queryKey: ["fleet-workspaces"],
    queryFn: fleetWorkspaces,
  })
  const [workspace, setWorkspace] = useState("")
  const [key, setKey] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const desktop =
    typeof window !== "undefined" ? window.openSweDesktop : undefined
  if (!desktop?.fleetConnect)
    return (
      <p className="p-space-6 text-secondary">
        Fleet is available in the updated OpenSWE Desktop app.
      </p>
    )
  const connected = connection.data?.[0]
  if (connected)
    return (
      <FleetChat
        key={connected.id}
        workspaceId={connected.id}
        threadId={workspaceId === connected.id ? threadId : undefined}
        onSelect={onSelect}
      />
    )
  return (
    <form
      className="mx-auto flex w-full max-w-md flex-col gap-space-4 p-space-6"
      onSubmit={async (event) => {
        event.preventDefault()
        setBusy(true)
        setError(null)
        const apiKey = key
        setKey("")
        try {
          const result = await desktop.fleetConnect({
            workspaceId: workspace.trim(),
            apiKey,
          })
          cache.setQueryData(["fleet-workspaces"], [result])
          onSelect(result.id)
        } catch (cause) {
          setError(
            cause instanceof Error
              ? cause.message
              : "Could not connect to LangSmith."
          )
        } finally {
          setBusy(false)
        }
      }}
    >
      <h1 className="text-xl font-semibold text-primary">Connect Fleet</h1>
      <p className="text-sm text-secondary">
        Use a LangSmith API key with Fleet and workspace metadata access. One US
        cloud workspace per session.
      </p>
      <Input
        size="md"
        label="Workspace UUID"
        value={workspace}
        onChange={setWorkspace}
        required
      />
      <Input
        size="md"
        label="LangSmith API Key"
        type="password"
        debounceMs={0}
        autoComplete="off"
        value={key}
        onChange={setKey}
        required
      />
      <p className="text-xs text-secondary">
        Your key stays in desktop memory, not on disk. Reconnect after quitting
        the app.
      </p>
      {error && (
        <p role="alert" className="text-error text-sm">
          {error}
        </p>
      )}
      <Button
        type="submit"
        size="md"
        loading={busy}
        disabled={busy || !workspace || !key || connection.isPending}
      >
        Connect
      </Button>
    </form>
  )
}
