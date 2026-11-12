import { Link, router, Stack, useLocalSearchParams } from "expo-router"
import { useState } from "react"
import {
  Pressable,
  RefreshControl,
  SectionList,
  StyleSheet,
  Text,
  View,
} from "react-native"
import ReanimatedSwipeable from "react-native-gesture-handler/ReanimatedSwipeable"

import {
  CATEGORY_TITLES,
  INBOX_CATEGORIES,
  threadCategory,
  workspaceKey,
  workspaceName,
  type AgentThread,
} from "../core/models"
import { Separator, Symbol } from "../design/components"
import { Theme } from "../design/theme"
import { AgentRow } from "../features/AgentRow"
import { threadContextMenu } from "../features/threadActions"
import {
  parseListScope,
  scopeTitle,
  threadHref,
  type AgentListScope,
} from "../navigation/routes"
import type { AgentStore } from "../state/agentStore"
import { useAgentStore } from "../state/appModel"
import { preferencesAllow } from "../state/listPreferences"

interface ListSection {
  id: string
  title: string | null
  data: AgentThread[]
}

export default function AgentListScreen() {
  const scope = parseListScope(useLocalSearchParams())
  const store = useAgentStore()
  const [query, setQuery] = useState("")
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const sections = listSections(store, scope, query)
  const toggle = (id: string) =>
    setCollapsed((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  return (
    <>
      <Stack.Screen
        options={{
          title: scopeTitle(scope),
          headerSearchBarOptions: {
            placeholder: "Search agents",
            autoFocus: scope.scope === "search",
            hideWhenScrolling: false,
            onChangeText: (event) => setQuery(event.nativeEvent.text),
            onCancelButtonPress: () => setQuery(""),
          },
        }}
      />
      <Stack.Toolbar placement="right">
        <Stack.Toolbar.Button
          icon="line.3.horizontal.decrease"
          accessibilityLabel="Customize"
          onPress={() => router.push("/customize")}
        />
        <Stack.Toolbar.Button
          icon="square.and.pencil"
          accessibilityLabel="New Agent"
          onPress={() => router.push("/new-agent")}
        />
      </Stack.Toolbar>
      <SectionList
        contentInsetAdjustmentBehavior="automatic"
        style={{ backgroundColor: Theme.background }}
        sections={sections.map((section) => ({
          ...section,
          data: collapsed.has(section.id) ? [] : section.data,
        }))}
        keyExtractor={(thread) => thread.id}
        stickySectionHeadersEnabled={false}
        refreshControl={
          <RefreshControl
            refreshing={false}
            onRefresh={() => store.refresh()}
          />
        }
        renderSectionHeader={({ section }) =>
          section.title ? (
            <SectionHeader
              title={section.title}
              collapsed={collapsed.has(section.id)}
              onPress={() => toggle(section.id)}
            />
          ) : null
        }
        renderItem={({ item }) => <SwipeableRow store={store} thread={item} />}
        ItemSeparatorComponent={() => <Separator inset={52} />}
        ListEmptyComponent={
          store.hasLoaded ? (
            <View style={styles.empty}>
              <Symbol
                name={query ? "magnifyingglass" : "tray"}
                size={40}
                color={Theme.textTertiary}
              />
              <Text style={styles.emptyTitle}>
                {query ? "No Results" : "No Agents"}
              </Text>
              <Text style={styles.emptyDetail}>
                {query
                  ? `Nothing matches “${query}”.`
                  : "Agents you start will show up here."}
              </Text>
            </View>
          ) : undefined
        }
      />
    </>
  )
}

function SwipeableRow({
  store,
  thread,
}: {
  store: AgentStore
  thread: AgentThread
}) {
  const pinned = store.isPinned(thread.id)
  return (
    <ReanimatedSwipeable
      friction={1.5}
      overshootFriction={8}
      renderLeftActions={() => (
        <SwipeAction
          color={Theme.attention}
          icon={pinned ? "pin.slash" : "pin"}
          title={pinned ? "Unpin" : "Pin"}
          onPress={() => store.togglePin(thread.id)}
        />
      )}
      renderRightActions={() => (
        <SwipeAction
          color={Theme.neutralAction}
          icon="archivebox"
          title="Archive"
          onPress={() => store.archive(thread.id)}
        />
      )}
    >
      <Link href={threadHref(thread.id)} asChild>
        <Link.Trigger>
          {/* A plain style: the link's slot drops style functions. Opaque, so the
              swipe actions stay hidden until the row moves. */}
          <Pressable style={styles.row}>
            <AgentRow
              thread={thread}
              preferences={store.preferences}
              pinned={pinned}
            />
          </Pressable>
        </Link.Trigger>
        {threadContextMenu(store, thread)}
      </Link>
    </ReanimatedSwipeable>
  )
}

function SwipeAction({
  color,
  icon,
  title,
  onPress,
}: {
  color: typeof Theme.brand
  icon: "pin" | "pin.slash" | "archivebox"
  title: string
  onPress: () => void
}) {
  return (
    <Pressable
      onPress={onPress}
      style={[styles.swipeAction, { backgroundColor: color }]}
    >
      <Symbol name={icon} size={18} color="#FFFFFF" />
      <Text style={styles.swipeTitle}>{title}</Text>
    </Pressable>
  )
}

function SectionHeader({
  title,
  collapsed,
  onPress,
}: {
  title: string
  collapsed: boolean
  onPress: () => void
}) {
  return (
    <Pressable onPress={onPress} style={styles.sectionHeader}>
      <Text style={styles.sectionTitle}>{title}</Text>
      <View style={{ transform: [{ rotate: collapsed ? "-90deg" : "0deg" }] }}>
        <Symbol
          name="chevron.down"
          size={12}
          color={Theme.textSecondary}
          weight="semibold"
        />
      </View>
    </Pressable>
  )
}

/** Filter, search and group the store's threads the way the scope and preferences ask. */
function listSections(
  store: AgentStore,
  scope: AgentListScope,
  rawQuery: string
): ListSection[] {
  const needle = rawQuery.trim().toLowerCase()
  const visible = store.threads
    .filter((thread) => {
      if (scope.scope === "category")
        return threadCategory(thread) === scope.category
      if (scope.scope === "workspace") return workspaceKey(thread) === scope.key
      return true
    })
    .filter((thread) => preferencesAllow(store.preferences, thread))
    .filter(
      (thread) =>
        !needle ||
        thread.title.toLowerCase().includes(needle) ||
        thread.repoFullName.toLowerCase().includes(needle) ||
        thread.branch.toLowerCase().includes(needle)
    )
    .sort((a, b) => {
      const pinnedA = store.isPinned(a.id)
      const pinnedB = store.isPinned(b.id)
      if (pinnedA !== pinnedB) return pinnedA ? -1 : 1
      return b.updatedAt.getTime() - a.updatedAt.getTime()
    })
  if (visible.length === 0) return []
  const single = [{ id: "all", title: null, data: visible }]
  switch (store.preferences.grouping) {
    case "category":
      if (scope.scope === "category") return single
      return INBOX_CATEGORIES.map((category) => ({
        id: category,
        title: CATEGORY_TITLES[category],
        data: visible.filter((thread) => threadCategory(thread) === category),
      })).filter((section) => section.data.length > 0)
    case "repo": {
      if (scope.scope === "workspace") return single
      const groups = new Map<string, AgentThread[]>()
      for (const thread of visible)
        groups.set(workspaceKey(thread), [
          ...(groups.get(workspaceKey(thread)) ?? []),
          thread,
        ])
      return [...groups.entries()].map(([key, threads]) => ({
        id: `repo:${key}`,
        title: workspaceName(threads[0]),
        data: threads,
      }))
    }
    case "none":
      return single
  }
}

const styles = StyleSheet.create({
  row: { paddingHorizontal: 16, backgroundColor: Theme.background },
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 20,
    paddingTop: 16,
    paddingBottom: 4,
  },
  sectionTitle: { fontSize: 16, color: Theme.textSecondary },
  swipeAction: {
    width: 84,
    alignItems: "center",
    justifyContent: "center",
    gap: 4,
  },
  swipeTitle: { fontSize: 13, fontWeight: "500", color: "#FFFFFF" },
  empty: {
    alignItems: "center",
    gap: 8,
    paddingTop: 120,
    paddingHorizontal: 32,
  },
  emptyTitle: { fontSize: 20, fontWeight: "600", color: Theme.textPrimary },
  emptyDetail: {
    fontSize: 15,
    color: Theme.textSecondary,
    textAlign: "center",
  },
})
