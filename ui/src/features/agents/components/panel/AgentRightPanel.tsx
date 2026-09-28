import { useCallback, useEffect, useMemo, useState } from "react"
import {
  ArrowsInIcon,
  ArrowsOutIcon,
  SidebarSimpleIcon,
} from "@phosphor-icons/react"
import type { ReactNode } from "react"

import type {
  PanelThreadRef,
  RightPanelSurface,
} from "@/features/agents/lib/rightPanelStore"
import type { TerminalGroupsController } from "@/features/agents/lib/terminalGroups"
import type { TerminalTarget } from "@/features/agents/lib/terminalSession"
import type { BrowserTabLabel } from "@/features/agents/components/panel/RightPanelTabs"
import { Tooltip, TooltipPopup, TooltipTrigger } from "@/components/ui/tooltip"
import { BrowserPanel } from "@/features/agents/browser/BrowserPanel"
import {
  type BrowserHostKind,
  browserTabUrl,
  selectThreadBrowserTabs,
  useBrowserTabStore,
} from "@/features/agents/browser/browserTabStore"
import { browserUrlHost } from "@/features/agents/browser/browserUrl"
import { useCloudBrowserSession } from "@/features/agents/browser/cloudBrowserSession"
import {
  TerminalActions,
  TerminalPanel,
} from "@/features/agents/components/TerminalPanel"
import { FilesPanel } from "@/features/agents/components/files/FilesPanel"
import { RightPanelTabs } from "@/features/agents/components/panel/RightPanelTabs"
import { RightPanelSheet } from "@/features/agents/components/panel/RightPanelSheet"
import { RIGHT_PANEL_INLINE_LAYOUT_MEDIA_QUERY } from "@/features/agents/components/panel/rightPanelLayout"
import {
  scopedThreadKey,
  selectThreadRightPanelState,
  useRightPanelStore,
} from "@/features/agents/lib/rightPanelStore"
import { terminalTabTitle } from "@/features/agents/lib/terminalTabTitle"
import { workspaceTargetKey } from "@/features/agents/lib/workspaceFiles"
import { useRegisterAppCommands } from "@/lib/appCommands"
import { cn } from "@/lib/utils"

export interface AgentRightPanelProps {
  threadRef: PanelThreadRef
  terminals: TerminalGroupsController
  terminalTarget: TerminalTarget
  cwd: string
  terminalAvailable: boolean
  diffAvailable: boolean
  /** Whether a browser tab can be opened: a desktop bridge or a live sandbox. */
  browserAvailable?: boolean
  /** Groups the browser's recently visited URLs; defaults to the thread. */
  browserHistoryScope?: string
  /** Rendered for the Changes surface; `fullScreen` drives layout-only extras. */
  renderDiff: (state: { fullScreen: boolean }) => ReactNode
  collapsed: boolean
  onCollapsedChange: (next: boolean) => void
  onTerminalOpenFile?: (path: string) => void
  onTerminalAddToChat?: (text: string) => void
}

function useIsNarrowLayout(): boolean {
  const [narrow, setNarrow] = useState(() =>
    typeof window === "undefined"
      ? false
      : window.matchMedia(RIGHT_PANEL_INLINE_LAYOUT_MEDIA_QUERY).matches
  )
  useEffect(() => {
    const media = window.matchMedia(RIGHT_PANEL_INLINE_LAYOUT_MEDIA_QUERY)
    const onChange = () => setNarrow(media.matches)
    onChange()
    media.addEventListener("change", onChange)
    return () => media.removeEventListener("change", onChange)
  }, [])
  return narrow
}

function PanelControl(props: {
  label: string
  onClick: () => void
  children: ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        aria-label={props.label}
        className="rounded-md p-1.5 text-muted-foreground/70 transition-colors hover:bg-accent hover:text-foreground"
        onClick={props.onClick}
        type="button"
      >
        {props.children}
      </TooltipTrigger>
      <TooltipPopup>{props.label}</TooltipPopup>
    </Tooltip>
  )
}

/**
 * The right-hand column shared by cloud threads and local desktop sessions.
 * Surfaces (changes, terminals) live in the right-panel store so
 * a thread's tabs survive navigation; this component owns only the layout
 * controls and the mapping from a surface to the component that renders it.
 */
export function AgentRightPanel(props: AgentRightPanelProps) {
  const {
    threadRef,
    terminals,
    terminalTarget,
    cwd,
    terminalAvailable,
    diffAvailable,
    collapsed,
    onCollapsedChange,
  } = props

  const browserAvailable = props.browserAvailable ?? false
  const browserHost: BrowserHostKind =
    threadRef.scope === "local" ? "desktop" : "cloud"
  const browserHistoryScope =
    props.browserHistoryScope ?? scopedThreadKey(threadRef)

  const byThreadKey = useRightPanelStore((state) => state.byThreadKey)
  const openSurface = useRightPanelStore((state) => state.open)
  const openBrowserSurface = useRightPanelStore((state) => state.openBrowser)
  const openTerminalSurface = useRightPanelStore((state) => state.openTerminal)
  const openFileSurface = useRightPanelStore((state) => state.openFile)
  const activateSurface = useRightPanelStore((state) => state.activateSurface)
  const closeSurfaceById = useRightPanelStore((state) => state.closeSurface)
  const closeOtherSurfaces = useRightPanelStore(
    (state) => state.closeOtherSurfaces
  )
  const closeSurfacesToRight = useRightPanelStore(
    (state) => state.closeSurfacesToRight
  )
  const closeAllSurfaces = useRightPanelStore((state) => state.closeAllSurfaces)
  const reconcileTerminalSurfaces = useRightPanelStore(
    (state) => state.reconcileTerminalSurfaces
  )
  const reconcileBrowserSurfaces = useRightPanelStore(
    (state) => state.reconcileBrowserSurfaces
  )
  const browserTabs = useBrowserTabStore((state) =>
    selectThreadBrowserTabs(state.tabsByThreadKey, threadRef)
  )
  const removeBrowserTab = useBrowserTabStore((state) => state.removeTab)

  const panelState = useMemo(
    () => selectThreadRightPanelState(byThreadKey, threadRef),
    [byThreadKey, threadRef]
  )
  const surfaces = panelState.surfaces
  const activeSurfaceId = panelState.activeSurfaceId
  const activeSurface =
    surfaces.find((surface) => surface.id === activeSurfaceId) ?? null

  const [maximized, setMaximized] = useState(false)
  const narrow = useIsNarrowLayout()

  // Terminal groups are the source of truth for which terminal surfaces exist:
  // a pty closed from inside the terminal must not leave a dead tab behind.
  const terminalGroupIds = terminals.state.terminalGroups
    .map((group) => group.id)
    .join(",")
  useEffect(() => {
    reconcileTerminalSurfaces(
      threadRef,
      terminalGroupIds ? terminalGroupIds.split(",") : []
    )
  }, [reconcileTerminalSurfaces, terminalGroupIds, threadRef])

  const terminalLabelsById = useMemo(() => {
    const labels = new Map<string, string>()
    for (const group of terminals.state.terminalGroups) {
      labels.set(group.id, terminalTabTitle(terminals, group.id))
    }
    return labels
  }, [terminals])

  const hasBrowserSurface = surfaces.some(
    (surface) => surface.kind === "preview"
  )
  // The sandbox browser connection lives as long as the strip lists a tab,
  // so pages the agent opens show up even while another surface is active.
  const cloudSession = useCloudBrowserSession(
    threadRef,
    browserHost === "cloud" && browserAvailable && hasBrowserSurface
  )

  // Desktop tabs persist beside their surfaces; a surface whose tab record is
  // gone (cleared storage, older build) has nothing to render and is dropped.
  const desktopTabIds =
    browserHost === "desktop" ? Object.keys(browserTabs).join(",") : null
  useEffect(() => {
    if (desktopTabIds === null) return
    reconcileBrowserSurfaces(
      threadRef,
      desktopTabIds ? desktopTabIds.split(",") : []
    )
  }, [desktopTabIds, reconcileBrowserSurfaces, threadRef])

  const browserLabelsById = useMemo(() => {
    const labels = new Map<string, BrowserTabLabel>()
    for (const tab of Object.values(browserTabs)) {
      const url = browserTabUrl(tab)
      const title =
        (tab.nav.kind === "idle" ? "" : tab.nav.title) ||
        (url ? browserUrlHost(url) : "")
      labels.set(tab.tabId, { title: title || "Browser", favicon: tab.favicon })
    }
    return labels
  }, [browserTabs])

  const handleAddTerminal = useCallback(() => {
    openTerminalSurface(threadRef, terminals.addGroup())
  }, [openTerminalSurface, terminals, threadRef])

  const handleAddDiff = useCallback(() => {
    openSurface(threadRef, "diff")
  }, [openSurface, threadRef])

  const handleAddFiles = useCallback(() => {
    openSurface(threadRef, "files")
  }, [openSurface, threadRef])

  const handleAddBrowser = useCallback(() => {
    openBrowserSurface(threadRef, null)
  }, [openBrowserSurface, threadRef])

  const handleActivate = useCallback(
    (surface: RightPanelSurface) => {
      activateSurface(threadRef, surface.id)
      if (surface.kind !== "terminal") return
      const groupId = surface.resourceId
      const terminalId = terminals.state.terminalGroups.find(
        (group) => group.id === groupId
      )?.terminalIds[0]
      if (terminalId) terminals.focus(terminalId)
    },
    [activateSurface, terminals, threadRef]
  )

  const handleCloseSurface = useCallback(
    (surface: RightPanelSurface) => {
      if (surface.kind === "terminal") {
        // The pty owns the tab: only drop the surface once it is really gone.
        void terminals.closeGroup(surface.resourceId).then((closed) => {
          if (closed) closeSurfaceById(threadRef, surface.id)
        })
        return
      }
      if (surface.kind === "preview" && surface.resourceId) {
        if (browserHost === "desktop") {
          // Dropping the record unmounts the webview, which closes the guest.
          removeBrowserTab(threadRef, surface.resourceId)
        } else {
          void cloudSession?.closeTab(surface.resourceId).catch(() => undefined)
        }
      }
      closeSurfaceById(threadRef, surface.id)
    },
    [
      browserHost,
      closeSurfaceById,
      cloudSession,
      removeBrowserTab,
      terminals,
      threadRef,
    ]
  )

  const toggleTerminal = useCallback(() => {
    if (!collapsed && activeSurface?.kind === "terminal") {
      onCollapsedChange(true)
      return
    }
    onCollapsedChange(false)
    const existing = surfaces.find((surface) => surface.kind === "terminal")
    if (existing) handleActivate(existing)
    else handleAddTerminal()
  }, [
    activeSurface?.kind,
    collapsed,
    handleActivate,
    handleAddTerminal,
    onCollapsedChange,
    surfaces,
  ])

  const toggleBrowser = useCallback(() => {
    if (!collapsed && activeSurface?.kind === "preview") {
      onCollapsedChange(true)
      return
    }
    onCollapsedChange(false)
    const existing = surfaces.find((surface) => surface.kind === "preview")
    if (existing) handleActivate(existing)
    else handleAddBrowser()
  }, [
    activeSurface?.kind,
    collapsed,
    handleActivate,
    handleAddBrowser,
    onCollapsedChange,
    surfaces,
  ])

  useRegisterAppCommands(
    useMemo(
      () => [
        {
          id: "toggle-work-panel",
          label: "Toggle work panel",
          aliases: ["show panel", "hide panel", "right panel"],
          shortcuts: ["mod+alt+b"],
          group: "Workspace",
          run: () => onCollapsedChange(!collapsed),
        },
        ...(diffAvailable
          ? [
              {
                id: "toggle-changes",
                label: "Toggle changes",
                aliases: ["diff", "review changes"],
                shortcuts: ["mod+d"],
                group: "Workspace",
                run: () => {
                  if (!collapsed && activeSurface?.kind === "diff") {
                    onCollapsedChange(true)
                  } else {
                    onCollapsedChange(false)
                    handleAddDiff()
                  }
                },
              },
            ]
          : []),
        ...(terminalAvailable
          ? [
              {
                id: "toggle-terminal",
                label: "Toggle terminal",
                aliases: ["open terminal", "hide terminal"],
                shortcuts: ["mod+j", "ctrl+`"],
                group: "Workspace",
                run: toggleTerminal,
              },
            ]
          : []),
        ...(browserAvailable
          ? [
              {
                id: "toggle-browser",
                label: "Toggle browser",
                aliases: ["open browser", "preview", "web"],
                shortcuts: ["mod+alt+p"],
                group: "Workspace",
                run: toggleBrowser,
              },
            ]
          : []),
      ],
      [
        activeSurface?.kind,
        browserAvailable,
        collapsed,
        diffAvailable,
        handleAddDiff,
        onCollapsedChange,
        terminalAvailable,
        toggleBrowser,
        toggleTerminal,
      ]
    )
  )

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => onCollapsedChange(false)}
        aria-label="Show panel"
        title="Show panel"
        className="fixed top-2 right-2 z-30 flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
      >
        <SidebarSimpleIcon className="size-4" />
      </button>
    )
  }

  const layoutControls = (
    <div className="flex shrink-0 items-center">
      {activeSurface?.kind === "terminal" ? (
        <TerminalActions
          groupId={activeSurface.resourceId}
          terminals={terminals}
        />
      ) : null}
      {narrow ? null : (
        <PanelControl
          label={maximized ? "Exit full screen" : "Expand panel"}
          onClick={() => setMaximized((value) => !value)}
        >
          {maximized ? (
            <ArrowsInIcon className="size-4" />
          ) : (
            <ArrowsOutIcon className="size-4" />
          )}
        </PanelControl>
      )}
      <PanelControl
        label="Hide panel"
        onClick={() => {
          setMaximized(false)
          onCollapsedChange(true)
        }}
      >
        <SidebarSimpleIcon className="size-4" />
      </PanelControl>
    </div>
  )

  const body = (
    <>
      {activeSurface?.kind === "diff"
        ? props.renderDiff({ fullScreen: maximized })
        : null}
      {activeSurface?.kind === "files" || activeSurface?.kind === "file" ? (
        <FilesPanel
          key={workspaceTargetKey(terminalTarget)}
          target={terminalTarget}
          relativePath={
            activeSurface.kind === "file" ? activeSurface.relativePath : null
          }
          revealRequestId={
            activeSurface.kind === "file" ? activeSurface.revealRequestId : 0
          }
          onOpenFile={(path) => openFileSurface(threadRef, path)}
        />
      ) : null}
      {/* Browser tabs stay mounted while hidden so a page keeps its state and
          a sandbox screencast does not reattach on every switch. */}
      {surfaces
        .filter(
          (
            surface
          ): surface is Extract<RightPanelSurface, { kind: "preview" }> =>
            surface.kind === "preview"
        )
        .map((surface) => (
          <div
            key={surface.id}
            className={cn(
              "flex min-h-0 flex-1 flex-col",
              surface.id !== activeSurfaceId && "hidden"
            )}
          >
            {browserAvailable ? (
              <BrowserPanel
                threadRef={threadRef}
                host={browserHost}
                tabId={surface.resourceId}
                visible={surface.id === activeSurfaceId && !collapsed}
                historyScope={browserHistoryScope}
                cloudSession={cloudSession}
              />
            ) : (
              <div className="flex min-h-0 flex-1 items-center justify-center px-6 text-center text-xs text-muted-foreground">
                The browser is only available while the workspace is running.
              </div>
            )}
          </div>
        ))}
      {/* Terminals stay mounted while hidden so their scrollback survives tab
          switches; every other surface unmounts. */}
      {surfaces
        .filter((surface) => surface.kind === "terminal")
        .map((surface) => (
          <div
            key={surface.id}
            className={cn(
              "min-h-0 flex-1",
              surface.id !== activeSurfaceId && "hidden"
            )}
          >
            <TerminalPanel
              target={terminalTarget}
              cwd={cwd}
              groupId={surface.resourceId}
              terminals={terminals}
              {...(props.onTerminalOpenFile
                ? { onOpenFile: props.onTerminalOpenFile }
                : {})}
              {...(props.onTerminalAddToChat
                ? { onAddToChat: props.onTerminalAddToChat }
                : {})}
            />
          </div>
        ))}
    </>
  )

  const tabs = (
    <RightPanelTabs
      mode={narrow ? "sheet" : "inline"}
      maximized={maximized}
      surfaces={surfaces}
      activeSurfaceId={activeSurfaceId}
      pendingSurfaceIds={EMPTY_PENDING_SURFACE_IDS}
      terminalLabelsById={terminalLabelsById}
      browserLabelsById={browserLabelsById}
      onActivate={handleActivate}
      onCloseSurface={handleCloseSurface}
      onCloseOtherSurfaces={(surface) =>
        closeOtherSurfaces(threadRef, surface.id)
      }
      onCloseSurfacesToRight={(surface) =>
        closeSurfacesToRight(threadRef, surface.id)
      }
      onCloseAllSurfaces={() => closeAllSurfaces(threadRef)}
      onCopyFilePath={(relativePath) => {
        const nav = navigator as { clipboard?: Clipboard }
        if (nav.clipboard) void nav.clipboard.writeText(relativePath)
      }}
      onAddTerminal={handleAddTerminal}
      onAddDiff={handleAddDiff}
      onAddFiles={handleAddFiles}
      onAddBrowser={handleAddBrowser}
      terminalAvailable={terminalAvailable}
      diffAvailable={diffAvailable}
      browserAvailable={browserAvailable}
      layoutControls={layoutControls}
    >
      {body}
    </RightPanelTabs>
  )

  if (narrow) {
    return (
      <RightPanelSheet open onClose={() => onCollapsedChange(true)}>
        {tabs}
      </RightPanelSheet>
    )
  }
  return tabs
}

const EMPTY_PENDING_SURFACE_IDS: ReadonlySet<string> = new Set<string>()
