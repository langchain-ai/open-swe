import { SymbolView, type SFSymbol } from "expo-symbols"
import { useState } from "react"
import {
  ActivityIndicator,
  Animated,
  LayoutAnimation,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native"

import { fileName, type DiffFile } from "../core/models"
import {
  activitySummary,
  activityVerb,
  type ActivityGroup,
  type TodoItem,
  type ToolKind,
  type TranscriptItem,
} from "../core/transcript"
import {
  Card,
  ChangeCounts,
  FileIcon,
  Separator,
  Symbol,
  usePulse,
} from "../design/components"
import { MarkdownText } from "../design/MarkdownText"
import { Theme } from "../design/theme"

export function TranscriptItemView({ item }: { item: TranscriptItem }) {
  switch (item.kind) {
    case "user":
      return <UserBubble text={item.text} />
    case "assistant":
      return <MarkdownText source={item.text} />
    case "activity":
      return <ActivityRow group={item.group} />
    case "todos":
      return <TodoCard todos={item.todos} />
  }
}

const snappy = () =>
  LayoutAnimation.configureNext(
    LayoutAnimation.create(220, "easeInEaseOut", "opacity")
  )

function UserBubble({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <View
      style={{
        flexDirection: "row",
        justifyContent: "flex-end",
        paddingLeft: 40,
      }}
    >
      <Pressable
        onPress={() => {
          snappy()
          setExpanded(!expanded)
        }}
        style={styles.bubble}
      >
        <Text
          style={styles.bubbleText}
          numberOfLines={expanded ? undefined : 8}
          selectable
        >
          {text}
        </Text>
      </Pressable>
    </View>
  )
}

const TOOL_SYMBOLS: Record<ToolKind, SFSymbol> = {
  read: "doc.text",
  search: "magnifyingglass",
  edit: "pencil",
  command: "terminal",
  other: "wrench.and.screwdriver",
}

/** "Explored 6 files, 3 searches, 1 other tool", expandable to each call. */
function ActivityRow({ group }: { group: ActivityGroup }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <View style={{ gap: 8 }}>
      <Pressable
        onPress={() => {
          snappy()
          setExpanded(!expanded)
        }}
        style={{ flexDirection: "row", alignItems: "center", gap: 6 }}
      >
        <Text style={[styles.activity, { color: Theme.textSecondary }]}>
          {activityVerb(group)}
        </Text>
        <Text
          style={[
            styles.activity,
            { color: Theme.textTertiary, flexShrink: 1 },
          ]}
          numberOfLines={1}
        >
          {activitySummary(group)}
        </Text>
        <View style={{ transform: [{ rotate: expanded ? "90deg" : "0deg" }] }}>
          <Symbol
            name="chevron.right"
            size={11}
            color={Theme.textTertiary}
            weight="semibold"
          />
        </View>
      </Pressable>
      {expanded ? (
        <View style={{ gap: 6, paddingLeft: 4 }}>
          {group.tools.map((tool, index) => (
            <View
              key={index}
              style={{ flexDirection: "row", alignItems: "center", gap: 8 }}
            >
              <View style={{ width: 18, alignItems: "center" }}>
                <Symbol
                  name={TOOL_SYMBOLS[tool.kind]}
                  size={13}
                  color={Theme.textTertiary}
                />
              </View>
              <Text
                style={[
                  styles.toolDetail,
                  tool.detail ? { fontFamily: "Menlo" } : null,
                ]}
                numberOfLines={1}
                ellipsizeMode="middle"
              >
                {tool.detail ?? tool.name}
              </Text>
            </View>
          ))}
        </View>
      ) : null}
    </View>
  )
}

function TodoCard({ todos }: { todos: TodoItem[] }) {
  const done = todos.filter((todo) => todo.status === "completed").length
  return (
    <Card>
      <View
        style={{
          flexDirection: "row",
          gap: 6,
          paddingHorizontal: 18,
          paddingVertical: 16,
        }}
      >
        <Text style={{ fontSize: 17, color: Theme.textPrimary }}>To-dos</Text>
        <Text
          style={{
            fontSize: 17,
            color: Theme.textTertiary,
            fontVariant: ["tabular-nums"],
          }}
        >
          {done}/{todos.length}
        </Text>
      </View>
      <Separator />
      {todos.map((todo, index) => (
        <View key={index}>
          <View style={styles.todoRow}>
            <TodoMark status={todo.status} />
            <Text
              style={[
                styles.todoText,
                todo.status === "completed" && {
                  color: Theme.textTertiary,
                  textDecorationLine: "line-through",
                },
              ]}
            >
              {todo.content}
            </Text>
          </View>
          {index < todos.length - 1 ? (
            <View style={{ marginLeft: 56, marginRight: 18 }}>
              <Separator />
            </View>
          ) : null}
        </View>
      ))}
    </Card>
  )
}

function TodoMark({ status }: { status: TodoItem["status"] }) {
  switch (status) {
    case "completed":
      return (
        <SymbolView
          name="checkmark.circle.fill"
          type="palette"
          colors={[Theme.surface, Theme.muted]}
          size={22}
          style={{ width: 22, height: 22 }}
        />
      )
    case "in_progress":
      return (
        <View
          style={{
            width: 22,
            height: 22,
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <ActivityIndicator size="small" />
        </View>
      )
    case "pending":
      return <View style={styles.pendingMark} />
  }
}

export function WorkingIndicator() {
  const pulse = usePulse(900)
  return (
    <Animated.Text
      style={[
        styles.activity,
        {
          color: Theme.textPrimary,
          opacity: pulse.interpolate({
            inputRange: [0, 1],
            outputRange: [0.4, 1],
          }),
        },
      ]}
    >
      Working
    </Animated.Text>
  )
}

/** "Changes 2" with each file and its counts; opens the full diff. */
export function ChangesCard({
  files,
  onPress,
}: {
  files: DiffFile[]
  onPress: () => void
}) {
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => ({ opacity: pressed ? 0.7 : 1 })}
    >
      <Card>
        <View
          style={{
            flexDirection: "row",
            alignItems: "center",
            gap: 6,
            paddingHorizontal: 18,
            paddingVertical: 16,
          }}
        >
          <Text style={{ fontSize: 17, color: Theme.textPrimary }}>
            Changes
          </Text>
          <Text style={{ fontSize: 17, color: Theme.textTertiary, flex: 1 }}>
            {files.length}
          </Text>
          <Symbol
            name="chevron.right"
            size={13}
            color={Theme.textTertiary}
            weight="semibold"
          />
        </View>
        {files.slice(0, 8).map((file) => (
          <View key={file.path}>
            <Separator />
            <View
              style={{
                flexDirection: "row",
                alignItems: "center",
                gap: 12,
                paddingHorizontal: 18,
                paddingVertical: 14,
              }}
            >
              <FileIcon path={file.path} />
              <Text
                style={{ fontSize: 16, color: Theme.textPrimary, flex: 1 }}
                numberOfLines={1}
              >
                {fileName(file.path)}
              </Text>
              <ChangeCounts
                additions={file.additions}
                deletions={file.deletions}
                size={16}
              />
            </View>
          </View>
        ))}
        {files.length > 8 ? (
          <>
            <Separator />
            <Text
              style={{
                fontSize: 15,
                color: Theme.textTertiary,
                paddingHorizontal: 18,
                paddingVertical: 12,
              }}
            >
              {files.length - 8} more files
            </Text>
          </>
        ) : null}
      </Card>
    </Pressable>
  )
}

const styles = StyleSheet.create({
  bubble: {
    backgroundColor: Theme.surfaceRaised,
    borderRadius: 22,
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  bubbleText: { fontSize: 17, lineHeight: 23, color: Theme.textPrimary },
  activity: { fontSize: 17 },
  toolDetail: { fontSize: 14, color: Theme.textTertiary, flexShrink: 1 },
  todoRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 14,
    paddingVertical: 13,
    paddingHorizontal: 18,
  },
  todoText: { flex: 1, fontSize: 16, lineHeight: 22, color: Theme.textPrimary },
  pendingMark: {
    width: 21,
    height: 21,
    borderRadius: 10.5,
    borderWidth: 1.5,
    borderColor: Theme.muted,
  },
})
