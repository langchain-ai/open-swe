import * as Clipboard from "expo-clipboard"
import { Link, Stack } from "expo-router"
import type { SFSymbol } from "expo-symbols"
import type { ComponentType, ReactNode } from "react"
import { Alert, Share } from "react-native"

import type { AgentThread } from "../core/models"
import type { AgentStore } from "../state/agentStore"

interface Action {
  title: string
  icon: SFSymbol
  destructive?: boolean
  onPress: () => void
}

interface Submenu {
  submenu: string
  icon: SFSymbol
  actions: Action[]
}

/** An inline section of the menu: actions and submenus, divided from the next section. */
type Section = (Action | Submenu)[]

export function promptRename(store: AgentStore, thread: AgentThread) {
  Alert.prompt(
    "Rename Agent",
    undefined,
    [
      { text: "Cancel", style: "cancel" },
      {
        text: "Rename",
        onPress: (title?: string) => title && store.rename(thread.id, title),
      },
    ],
    "plain-text",
    thread.title
  )
}

/** The actions every thread offers, in its context menu and its detail menu. */
function threadSections(
  store: AgentStore,
  thread: AgentThread,
  showDetails?: () => void
): Section[] {
  const pinned = store.isPinned(thread.id)
  const link = store.service.threadPageBase
    ? `${store.service.threadPageBase}/${encodeURIComponent(thread.id)}`
    : null
  const copy = (text: string) => () => Clipboard.setStringAsync(text)
  const copyActions: Action[] = [
    ...(link
      ? [{ title: "Link", icon: "link" as SFSymbol, onPress: copy(link) }]
      : []),
    ...(thread.branch
      ? [
          {
            title: "Branch Name",
            icon: "arrow.triangle.branch" as SFSymbol,
            onPress: copy(thread.branch),
          },
        ]
      : []),
    ...(thread.pr
      ? [
          {
            title: "Pull Request URL",
            icon: "arrow.triangle.pull" as SFSymbol,
            onPress: copy(thread.pr.url),
          },
        ]
      : []),
    { title: "Agent ID", icon: "number", onPress: copy(thread.id) },
  ]
  return [
    [
      ...(showDetails
        ? [
            {
              title: "View Details",
              icon: "info.circle" as SFSymbol,
              onPress: showDetails,
            },
          ]
        : []),
      {
        title: "Rename",
        icon: "pencil",
        onPress: () => promptRename(store, thread),
      },
      {
        title: pinned ? "Unpin" : "Pin",
        icon: pinned ? "pin.slash" : "pin",
        onPress: () => store.togglePin(thread.id),
      },
    ],
    [
      { submenu: "Copy", icon: "doc.on.doc", actions: copyActions },
      ...(link
        ? [
            {
              title: "Share",
              icon: "square.and.arrow.up" as SFSymbol,
              onPress: () => Share.share({ url: link, message: thread.title }),
            },
          ]
        : []),
    ],
    [
      {
        title: "Archive",
        icon: "archivebox",
        onPress: () => store.archive(thread.id),
      },
    ],
  ]
}

interface MenuComponents {
  Menu: ComponentType<{
    title?: string
    icon?: SFSymbol
    inline?: boolean
    children?: ReactNode
  }>
  Action: ComponentType<{
    icon?: SFSymbol
    destructive?: boolean
    onPress?: () => void
    children?: ReactNode
  }>
}

function renderSections(
  sections: Section[],
  { Menu, Action }: MenuComponents
): ReactNode {
  const renderAction = (action: Action | Submenu, key: number): ReactNode =>
    "submenu" in action ? (
      <Menu key={key} title={action.submenu} icon={action.icon}>
        {action.actions.map((item, index) => renderAction(item, index))}
      </Menu>
    ) : (
      <Action
        key={key}
        icon={action.icon}
        destructive={action.destructive}
        onPress={action.onPress}
      >
        {action.title}
      </Action>
    )
  return sections.map((section, index) => (
    <Menu key={index} inline>
      {section.map((action, actionIndex) => renderAction(action, actionIndex))}
    </Menu>
  ))
}

/**
 * The long-press menu on an agent row. A `Link` finds its menu among its direct
 * children by type, so this returns the `Link.Menu` element itself rather than
 * a component that renders one.
 */
export function threadContextMenu(store: AgentStore, thread: AgentThread) {
  return (
    <Link.Menu>
      {renderSections(threadSections(store, thread), {
        Menu: Link.Menu as MenuComponents["Menu"],
        Action: Link.MenuAction as MenuComponents["Action"],
      })}
    </Link.Menu>
  )
}

/** The `•••` menu in the thread view's toolbar. */
export function ThreadToolbarMenu({
  store,
  thread,
  showDetails,
}: {
  store: AgentStore
  thread: AgentThread
  showDetails: () => void
}) {
  return (
    <Stack.Toolbar placement="right">
      <Stack.Toolbar.Menu icon="ellipsis" accessibilityLabel="More">
        {renderSections(threadSections(store, thread, showDetails), {
          Menu: Stack.Toolbar.Menu as MenuComponents["Menu"],
          Action: Stack.Toolbar.MenuAction as MenuComponents["Action"],
        })}
      </Stack.Toolbar.Menu>
    </Stack.Toolbar>
  )
}
