import type { ReactNode } from "react"
import { ActivityIndicator, StyleSheet, Text, View } from "react-native"

import {
  compactAge,
  gitState,
  GIT_STATE_TITLES,
  isEmptyDiff,
  isLocal,
  workspaceName,
  type AgentThread,
} from "../core/models"
import { ChangeCounts, Symbol } from "../design/components"
import { gitStateColor, gitStateSymbol, Palette, Theme } from "../design/theme"
import type { ListPreferences } from "../state/listPreferences"

export function AgentRow({
  thread,
  preferences,
  pinned,
}: {
  thread: AgentThread
  preferences: ListPreferences
  pinned: boolean
}) {
  return (
    <View style={styles.row}>
      <View style={styles.dotColumn}>
        <StatusDot thread={thread} />
      </View>
      <View style={{ flex: 1, gap: 6 }}>
        <View style={styles.titleLine}>
          {pinned ? (
            <Symbol name="pin.fill" size={11} color={Theme.textTertiary} />
          ) : null}
          <Text style={styles.title} numberOfLines={1}>
            {thread.title}
          </Text>
          {preferences.showLastUpdated ? (
            <Text style={styles.age}>{compactAge(thread.updatedAt)}</Text>
          ) : null}
        </View>
        <Subtitle thread={thread} preferences={preferences} />
      </View>
    </View>
  )
}

/** "Merged · +12 -1 · repo", "No Changes · repo", each part behind its Customize toggle. */
function Subtitle({
  thread,
  preferences,
}: {
  thread: AgentThread
  preferences: ListPreferences
}) {
  const state = gitState(thread)
  const parts: ReactNode[] = []
  if (preferences.showBranchStatus) {
    const symbol = gitStateSymbol(state)
    if (state === "noChanges")
      parts.push(
        <Text key="git" style={styles.meta}>
          {GIT_STATE_TITLES[state]}
        </Text>
      )
    else if (state !== "changes" && symbol) {
      parts.push(
        <View key="git" style={styles.inline}>
          <Symbol name={symbol} size={13} color={gitStateColor(state)} />
          <Text style={{ fontSize: 15, color: Theme.textSecondary }}>
            {GIT_STATE_TITLES[state]}
          </Text>
        </View>
      )
    }
  }
  if (
    preferences.showChanges &&
    thread.diffStats &&
    !isEmptyDiff(thread.diffStats)
  ) {
    parts.push(
      <ChangeCounts
        key="changes"
        additions={thread.diffStats.additions}
        deletions={thread.diffStats.deletions}
      />
    )
  }
  if (preferences.showRuntime && isLocal(thread)) {
    parts.push(
      <View key="local" style={styles.inline}>
        <Symbol name="desktopcomputer" size={13} color={Theme.textTertiary} />
        <Text style={styles.meta}>Local</Text>
      </View>
    )
  }
  if (thread.repoFullName) {
    parts.push(
      <Text
        key="repo"
        style={[styles.meta, { flexShrink: 1 }]}
        numberOfLines={1}
      >
        {workspaceName(thread)}
      </Text>
    )
  }
  return (
    <View style={styles.inline}>
      {parts.map((part, index) => (
        <View key={index} style={styles.inline}>
          {index > 0 ? <Text style={styles.meta}>·</Text> : null}
          {part}
        </View>
      ))}
    </View>
  )
}

/** Blue while unread, grey once seen, a spinner while the agent works. */
export function StatusDot({ thread }: { thread: AgentThread }) {
  if (thread.status === "running")
    return (
      <ActivityIndicator
        size="small"
        color={Palette.working}
        style={{ transform: [{ scale: 0.75 }] }}
      />
    )
  if (thread.status === "error")
    return (
      <Symbol
        name="exclamationmark.circle.fill"
        size={14}
        color={Theme.error}
      />
    )
  return (
    <View
      style={[
        styles.dot,
        { backgroundColor: thread.viewed ? Theme.muted : Palette.unread },
      ]}
    />
  )
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", paddingVertical: 14 },
  dotColumn: {
    width: 36,
    height: 22,
    justifyContent: "center",
    alignItems: "flex-start",
  },
  titleLine: { flexDirection: "row", alignItems: "center", gap: 6 },
  title: { flex: 1, fontSize: 17, color: Theme.textPrimary },
  age: { fontSize: 15, color: Theme.textTertiary },
  inline: { flexDirection: "row", alignItems: "center", gap: 6, flexShrink: 1 },
  meta: { fontSize: 15, color: Theme.textTertiary },
  dot: { width: 10, height: 10, borderRadius: 5 },
})
