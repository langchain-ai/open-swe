import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useState,
} from "react"
import { DownloadIcon } from "lucide-react"

import type { DesktopLocalThreadSummary } from "@/desktop"
import type { AgentThread } from "@/features/agents/lib/types"
import { agentsApi } from "@/features/agents/lib/api"
import {
  useAgentThreadPrDiff,
  useAgentThreadTurnDiff,
} from "@/features/agents/lib/queries"
import { useLocalThreadDiff } from "@/features/agents/lib/desktopLocal"
import {
  useRightPanelStore,
  type RightPanelSurface,
} from "@/features/agents/lib/rightPanelStore"
import { useTerminalGroups } from "@/features/agents/lib/terminalGroups"
import { terminalTabTitle } from "@/features/agents/lib/terminalTabTitle"
import { useRegisterAppCommands } from "@/lib/appCommands"
import { cn } from "@/lib/utils"
import { BrowserPanel } from "@/features/agents/components/BrowserPanel"
import { ChangesPanel } from "@/features/agents/components/ChangesPanel"
import { toPanelFiles } from "@/features/agents/components/DiffFilesView"
import {
  RightPanelShell,
  type RightPanelLauncherKind,
  type RightPanelLauncherItem,
} from "@/features/agents/components/RightPanelShell"
import { TerminalPanel } from "@/features/agents/components/TerminalPanel"
import {
  FileEditorPanel,
  FileExplorerPanel,
  type WorkspaceAdapter,
} from "@/features/agents/components/WorkspacePanel"

export type RightPanelTarget =
  | { kind: "cloud"; thread: AgentThread }
  | { kind: "local"; thread: DesktopLocalThreadSummary }

export interface RightPanelHostHandle {
  openFile: (path: string) => void
  openDiff: () => void
  openTerminal: () => void
  toggle: () => void
}

export const RightPanelHost = forwardRef<
  RightPanelHostHandle,
  {
    target: RightPanelTarget
    collapsed: boolean
    onCollapsedChange: (collapsed: boolean) => void
    isRunning: boolean
    revealFilePath?: string | null
    revealDiffKey?: number
    onAddToChat?: (text: string) => void
  }
>(function RightPanelHost(
  {
    target,
    collapsed,
    onCollapsedChange,
    isRunning,
    revealFilePath,
    revealDiffKey = 0,
    onAddToChat,
  },
  ref
) {
  const threadId = target.thread.id
  const scope = `${target.kind}:${threadId}`
  const panel = useRightPanelStore(scope)
  const cwd = target.kind === "local" ? target.thread.cwd : ""
  const terminals = useTerminalGroups(
    target.kind === "local"
      ? { kind: "local", sessionId: threadId }
      : { kind: "cloud", threadId },
    cwd
  )
  const activeSurface = panel.surfaces.find(
    (surface) => surface.id === panel.activeSurfaceId
  )
  const diffVisible = !collapsed && activeSurface?.kind === "diff"
  const pullRequestVisible =
    !collapsed && activeSurface?.kind === "pull-request"
  const cloudDiff = useAgentThreadTurnDiff(
    threadId,
    null,
    target.kind === "cloud" && diffVisible,
    {},
    isRunning
  )
  const localDiff = useLocalThreadDiff(
    threadId,
    target.kind === "local" && (diffVisible || pullRequestVisible),
    isRunning
  )
  const cloudPrDiff = useAgentThreadPrDiff(
    threadId,
    target.kind === "cloud" && pullRequestVisible
  )
  const diff = target.kind === "cloud" ? cloudDiff : localDiff
  const files = useMemo(
    () => toPanelFiles(diff.data?.files ?? []),
    [diff.data?.files]
  )
  const localRepository =
    target.kind === "local" ? localDiff.data?.repository : undefined
  const branch =
    target.kind === "cloud" ? target.thread.branch : localRepository?.branch
  const pullRequest =
    target.kind === "cloud" ? target.thread.pr : localRepository?.pr
  const [dirtyFiles, setDirtyFiles] = useState<ReadonlySet<string>>(new Set())
  const [recoveringPatch, setRecoveringPatch] = useState(false)
  const [recoveryError, setRecoveryError] = useState<string | null>(null)
  const handleDirtyChange = useCallback((path: string, dirty: boolean) => {
    setDirtyFiles((current) => {
      const next = new Set(current)
      if (dirty) next.add(path)
      else next.delete(path)
      return next
    })
  }, [])
  const workspace = useMemo<WorkspaceAdapter>(
    () =>
      target.kind === "cloud"
        ? {
            key: scope,
            list: (path) => agentsApi.listWorkspaceFiles(threadId, path),
            read: (path) => agentsApi.readWorkspaceFile(threadId, path),
            write: (path, content) =>
              agentsApi.writeWorkspaceFile(threadId, path, content),
          }
        : {
            key: scope,
            list: (path) =>
              window.openSweDesktop!.listLocalFiles(threadId, path),
            read: (path) =>
              window.openSweDesktop!.readLocalFile(threadId, path),
            write: (path, content) =>
              window.openSweDesktop!.writeLocalFile(threadId, path, content),
          },
    [scope, target.kind, threadId]
  )
  const openTerminal = useCallback(() => {
    const groupId = terminals.addGroup()
    panel.openTerminal(groupId)
    onCollapsedChange(false)
  }, [onCollapsedChange, panel.openTerminal, terminals])
  const toggleTerminal = useCallback(() => {
    if (!collapsed && activeSurface?.kind === "terminal") {
      onCollapsedChange(true)
      return
    }
    const existing = panel.surfaces.find(
      (surface) => surface.kind === "terminal"
    )
    if (existing) panel.activate(existing.id)
    else {
      const groupId = terminals.addGroup()
      panel.openTerminal(groupId)
    }
    onCollapsedChange(false)
  }, [
    activeSurface?.kind,
    collapsed,
    onCollapsedChange,
    panel.activate,
    panel.openTerminal,
    panel.surfaces,
    terminals,
  ])
  const openFile = useCallback(
    (path: string) => {
      const relativePath =
        target.kind === "local" && path.startsWith(`${cwd}/`)
          ? path.slice(cwd.length + 1)
          : path
      panel.openFile(relativePath)
      onCollapsedChange(false)
    },
    [cwd, onCollapsedChange, panel.openFile, target.kind]
  )
  const openDiff = useCallback(() => {
    panel.openDiff()
    onCollapsedChange(false)
  }, [onCollapsedChange, panel.openDiff])
  const toggle = useCallback(() => {
    if (!collapsed) onCollapsedChange(true)
    else onCollapsedChange(false)
  }, [collapsed, onCollapsedChange])
  useImperativeHandle(
    ref,
    () => ({ openFile, openDiff, openTerminal, toggle }),
    [openDiff, openFile, openTerminal, toggle]
  )
  useEffect(() => {
    if (revealDiffKey > 0) openDiff()
  }, [openDiff, revealDiffKey])
  useEffect(() => {
    if (revealFilePath) openFile(revealFilePath)
  }, [openFile, revealFilePath])
  const terminalGroupIds = terminals.state.terminalGroups
    .map((group) => group.id)
    .join(",")
  useEffect(() => {
    panel.reconcileTerminals(
      terminalGroupIds ? terminalGroupIds.split(",") : []
    )
  }, [panel.reconcileTerminals, terminalGroupIds])
  const terminalLabels = useMemo(
    () =>
      new Map(
        terminals.state.terminalGroups.map((group) => [
          group.id,
          terminalTabTitle(terminals, group.id),
        ])
      ),
    [terminals]
  )
  const terminalAvailable =
    target.kind === "local" ||
    (target.thread.isOwner !== false && Boolean(target.thread.sandboxId))
  const filesAvailable =
    target.kind === "local" || target.thread.isOwner !== false
  const launcherItems = useMemo<Array<RightPanelLauncherItem>>(
    () => [
      {
        kind: "preview",
        available: true,
        unavailableHint: "Browser previews are unavailable.",
        disabledReason: "Browser previews are unavailable.",
      },
      {
        kind: "terminal",
        available: terminalAvailable,
        unavailableHint: "Available when a workspace is connected.",
        disabledReason: "Terminal requires an owned, connected workspace.",
      },
      {
        kind: "files",
        available: filesAvailable,
        unavailableHint: "Available when a workspace is connected.",
        disabledReason: "Files require an owned workspace.",
      },
      {
        kind: "diff",
        available: true,
        unavailableHint: "Available for Git repositories.",
        disabledReason: "Diff is unavailable.",
      },
      {
        kind: "pull-request",
        available: Boolean(pullRequest),
        unavailableHint: "No pull request on this branch yet.",
        disabledReason: "This branch has no pull request yet.",
      },
    ],
    [filesAvailable, pullRequest, terminalAvailable]
  )
  const openKind = useCallback(
    (kind: RightPanelLauncherKind) => {
      if (kind === "terminal") openTerminal()
      else if (kind === "preview") panel.openBrowser()
      else if (kind === "files") panel.openFiles()
      else if (kind === "diff") panel.openDiff()
      else panel.openPullRequest()
      onCollapsedChange(false)
    },
    [
      onCollapsedChange,
      openTerminal,
      panel.openBrowser,
      panel.openDiff,
      panel.openFiles,
      panel.openPullRequest,
    ]
  )
  const closeSurface = useCallback(
    async (surface: RightPanelSurface) => {
      if (
        surface.kind === "file" &&
        dirtyFiles.has(surface.relativePath) &&
        !window.confirm("Discard unsaved changes?")
      )
        return
      if (
        surface.kind === "terminal" &&
        !(await terminals.closeGroup(surface.resourceId))
      )
        return
      panel.closeSurface(surface.id)
    },
    [dirtyFiles, panel.closeSurface, terminals]
  )
  const closeMany = useCallback(
    async (surfaces: ReadonlyArray<RightPanelSurface>) => {
      for (const surface of surfaces) await closeSurface(surface)
    },
    [closeSurface]
  )
  const downloadRecoveryPatch = useCallback(async () => {
    if (target.kind !== "cloud") return
    setRecoveringPatch(true)
    setRecoveryError(null)
    try {
      const { blob, filename } =
        await agentsApi.downloadThreadRecoveryPatch(threadId)
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
    } catch (error) {
      setRecoveryError(
        error instanceof Error ? error.message : "Failed to download patch"
      )
    } finally {
      setRecoveringPatch(false)
    }
  }, [target.kind, threadId])
  useRegisterAppCommands(
    useMemo(
      () => [
        {
          id: "toggle-work-panel",
          label: "Toggle work panel",
          aliases: ["show panel", "hide panel", "changes panel"],
          shortcuts: ["mod+alt+b"],
          group: "Workspace",
          run: toggle,
        },
        ...(terminalAvailable
          ? [
              {
                id: "toggle-terminal",
                label: "Toggle terminal",
                aliases: ["open terminal", "hide terminal"],
                shortcuts: ["ctrl+`"],
                group: "Workspace",
                run: toggleTerminal,
              },
            ]
          : []),
      ],
      [terminalAvailable, toggle, toggleTerminal]
    )
  )
  return (
    <RightPanelShell
      surfaces={panel.surfaces}
      activeSurfaceId={panel.activeSurfaceId}
      terminalLabels={terminalLabels}
      launcherItems={launcherItems}
      collapsed={collapsed}
      onCollapsedChange={onCollapsedChange}
      onActivate={(surface) => panel.activate(surface.id)}
      onClose={closeSurface}
      onCloseOthers={async (surface) => {
        await closeMany(
          panel.surfaces.filter((entry) => entry.id !== surface.id)
        )
        panel.activate(surface.id)
      }}
      onCloseToRight={async (surface) => {
        const index = panel.surfaces.findIndex(
          (entry) => entry.id === surface.id
        )
        await closeMany(panel.surfaces.slice(index + 1))
        panel.activate(surface.id)
      }}
      onCloseAll={() => closeMany([...panel.surfaces])}
      onOpen={openKind}
    >
      {activeSurface?.kind === "diff" && (
        <ChangesPanel
          files={files}
          status={diff.data?.status}
          isLoading={diff.isPending}
          isFetching={diff.isFetching}
          error={diff.error}
          truncated={diff.data?.truncated}
          branch={branch}
          pr={pullRequest}
          revealFilePath={revealFilePath}
          fullScreen={false}
          onRefresh={() => void diff.refetch()}
          extraActions={
            target.kind === "cloud" &&
            target.thread.status !== "running" &&
            target.thread.isOwner !== false ? (
              <button
                type="button"
                aria-label="Download recovery patch"
                title={recoveryError ?? "Download recovery patch"}
                disabled={recoveringPatch}
                onClick={() => void downloadRecoveryPatch()}
                className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
              >
                <DownloadIcon className="size-3.5" />
              </button>
            ) : undefined
          }
        />
      )}
      {activeSurface?.kind === "files" && (
        <FileExplorerPanel adapter={workspace} onOpen={openFile} />
      )}
      {panel.surfaces
        .filter((surface) => surface.kind === "file")
        .map((surface) => (
          <div
            key={surface.id}
            className={cn(
              "flex min-h-0 flex-1 flex-col",
              surface.id !== panel.activeSurfaceId && "hidden"
            )}
          >
            <FileEditorPanel
              adapter={workspace}
              path={surface.relativePath}
              onDirtyChange={handleDirtyChange}
            />
          </div>
        ))}
      {panel.surfaces
        .filter((surface) => surface.kind === "preview")
        .map((surface) => (
          <div
            key={surface.id}
            className={cn(
              "flex min-h-0 flex-1 flex-col",
              surface.id !== panel.activeSurfaceId && "hidden"
            )}
          >
            <BrowserPanel
              openExternal={(url) => {
                if (target.kind === "local")
                  void window.openSweDesktop?.openExternal(url)
                else window.open(url, "_blank", "noopener,noreferrer")
              }}
            />
          </div>
        ))}
      {panel.surfaces
        .filter((surface) => surface.kind === "terminal")
        .map((surface) => (
          <div
            key={surface.id}
            className={cn(
              "min-h-0 flex-1",
              surface.id !== panel.activeSurfaceId && "hidden"
            )}
          >
            <TerminalPanel
              target={
                target.kind === "local"
                  ? { kind: "local", sessionId: threadId }
                  : { kind: "cloud", threadId }
              }
              cwd={cwd}
              groupId={surface.resourceId}
              terminals={terminals}
              onOpenFile={openFile}
              onAddToChat={onAddToChat}
            />
          </div>
        ))}
      {activeSurface?.kind === "pull-request" && (
        <ChangesPanel
          files={
            target.kind === "cloud"
              ? toPanelFiles(cloudPrDiff.data?.files ?? [])
              : files
          }
          status={target.kind === "local" ? localDiff.data?.status : undefined}
          isLoading={
            target.kind === "cloud"
              ? cloudPrDiff.isPending
              : localDiff.isPending
          }
          isFetching={
            target.kind === "cloud"
              ? cloudPrDiff.isFetching
              : localDiff.isFetching
          }
          error={target.kind === "cloud" ? cloudPrDiff.error : localDiff.error}
          truncated={
            target.kind === "cloud"
              ? cloudPrDiff.data?.truncated
              : localDiff.data?.truncated
          }
          branch={branch}
          pr={pullRequest}
          fullScreen={false}
          onRefresh={() =>
            void (target.kind === "cloud"
              ? cloudPrDiff.refetch()
              : localDiff.refetch())
          }
        />
      )}
    </RightPanelShell>
  )
})
