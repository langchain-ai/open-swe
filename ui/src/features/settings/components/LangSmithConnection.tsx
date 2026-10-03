import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { SettingsRow } from "@/components/AppShell"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogDescription,
  DialogPopup,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { api, connectService } from "@/lib/api"
import type { LangSmithCredentialStatus, LangSmithRegion } from "@/lib/api"
import { optimisticUpdate } from "@/lib/optimistic"
import { StatusPill } from "./StatusPill"

export function LangSmithConnection() {
  const qc = useQueryClient()
  const creds = useQuery({
    queryKey: ["myLangSmith"],
    queryFn: api.getMyLangSmithStatus,
  })
  const [open, setOpen] = useState(false)
  const [region, setRegion] = useState<LangSmithRegion>("us")
  const [keyMode, setKeyMode] = useState(false)
  const [apiKey, setApiKey] = useState("")
  const [workspace, setWorkspace] = useState("")
  const [connecting, setConnecting] = useState(false)

  const changeOpen = (value: boolean) => {
    setApiKey("")
    setWorkspace("")
    setKeyMode(false)
    setRegion(creds.data?.region ?? "us")
    setOpen(value)
  }
  const save = useMutation({
    meta: { errorTitle: "Couldn't connect LangSmith" },
    mutationFn: api.connectLangSmithKey,
    onSuccess: (data) => {
      qc.setQueryData(["myLangSmith"], data)
      changeOpen(false)
    },
  })
  const disconnect = useMutation({
    meta: { errorTitle: "Couldn't disconnect LangSmith" },
    mutationFn: api.disconnectLangSmith,
    onMutate: async () => ({
      undo: await optimisticUpdate<LangSmithCredentialStatus>(
        qc,
        ["myLangSmith"],
        (current) => ({
          ...current,
          connected: false,
          reconnect_required: false,
        })
      ),
    }),
    onError: (_error, _variables, context) => context?.undo(),
    onSettled: () => qc.invalidateQueries({ queryKey: ["myLangSmith"] }),
  })
  const connect = () => {
    setConnecting(true)
    void connectService("langsmith", region)?.finally(() => {
      setConnecting(false)
      changeOpen(false)
      void qc.invalidateQueries({ queryKey: ["myLangSmith"] })
    })
  }
  const connected = !!creds.data?.connected
  const identity = creds.data?.email ?? creds.data?.name

  return (
    <>
      <SettingsRow
        label="LangSmith"
        description={
          creds.data?.reconnect_required
            ? "Authorization expired. Reconnect LangSmith to use its tools."
            : connected
              ? `Connected ${identity ? `as ${identity}` : "with an API key"} · ${creds.data?.region.toUpperCase()}. Used only in your private threads.`
              : "Use LangSmith MCP tools in your private threads with your own account. This does not change deployment tracing or sandbox credentials."
        }
        control={
          <div className="flex items-center gap-2">
            <StatusPill connected={connected} />
            <Button
              size="sm"
              variant={connected ? "outline" : "default"}
              onClick={() => changeOpen(true)}
              disabled={
                creds.isLoading ||
                creds.isError ||
                disconnect.isPending ||
                save.isPending ||
                connecting
              }
            >
              {connected || creds.data?.reconnect_required
                ? "Reconnect"
                : "Connect"}
            </Button>
            {creds.data?.method && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => disconnect.mutate()}
                disabled={disconnect.isPending || save.isPending || connecting}
              >
                Disconnect
              </Button>
            )}
          </div>
        }
      />
      <Dialog
        open={open}
        onOpenChange={(value) => {
          if (!save.isPending && !connecting) changeOpen(value)
        }}
      >
        <DialogPopup className="gap-4 p-6">
          <DialogTitle>Connect LangSmith</DialogTitle>
          <DialogDescription>
            Authorize your account or use an API key. Credentials are encrypted
            and available only in your private threads.
          </DialogDescription>
          <label className="grid gap-2 text-xs">
            Region
            <select
              aria-label="LangSmith region"
              value={region}
              onChange={(event) =>
                setRegion(event.target.value as LangSmithRegion)
              }
              className="rounded-md border bg-background p-2"
              disabled={save.isPending || connecting}
            >
              <option value="us">US</option>
              <option value="eu">EU</option>
              <option value="apac">APAC</option>
            </select>
          </label>
          {keyMode ? (
            <form
              className="grid gap-4"
              onSubmit={(event) => {
                event.preventDefault()
                save.mutate({
                  region,
                  api_key: apiKey,
                  workspace_id: workspace.trim() || null,
                })
                setApiKey("")
              }}
            >
              <label className="grid gap-2 text-xs">
                API key
                <Input
                  type="password"
                  autoComplete="off"
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  required
                  maxLength={8192}
                  disabled={save.isPending}
                />
              </label>
              <label className="grid gap-2 text-xs">
                Workspace ID (optional)
                <Input
                  value={workspace}
                  onChange={(event) => setWorkspace(event.target.value)}
                  placeholder="Workspace UUID"
                  disabled={save.isPending}
                  pattern="[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
                />
              </label>
              <p className="text-xs text-muted-foreground">
                For keys with access to multiple workspaces, select the
                workspace to use.
              </p>
              <div className="flex justify-end gap-2">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => changeOpen(false)}
                  disabled={save.isPending}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={save.isPending || !apiKey.trim()}
                >
                  {save.isPending ? "Validating…" : "Save API key"}
                </Button>
              </div>
            </form>
          ) : (
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => setKeyMode(true)}
                disabled={connecting}
              >
                Use API key instead
              </Button>
              <Button onClick={connect} disabled={connecting}>
                {connecting ? "Authorizing…" : "Authorize LangSmith"}
              </Button>
            </div>
          )}
        </DialogPopup>
      </Dialog>
    </>
  )
}
