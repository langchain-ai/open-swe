import { Stack, useLocalSearchParams } from "expo-router"
import { Pressable, ScrollView, StyleSheet, Text } from "react-native"

import {
  AGENT_STATUSES,
  GIT_STATE_TITLES,
  GIT_STATES,
  sourceTitle,
  STATUS_TITLES,
} from "../../core/models"
import { Separator, Symbol } from "../../design/components"
import { Theme } from "../../design/theme"
import type { AgentStore } from "../../state/agentStore"
import { useAgentStore } from "../../state/appModel"
import type { ListPreferences } from "../../state/listPreferences"

type FilterKey = "repos" | "statuses" | "gitStates" | "sources"

/** The filter a route names: its title, its options, and the preference it sets. */
function filterSpec(
  filter: string,
  store: AgentStore
): { title: string; key: FilterKey; options: [string, string][] } {
  switch (filter) {
    case "status":
      return {
        title: "Status",
        key: "statuses",
        options: AGENT_STATUSES.map((status) => [
          status,
          STATUS_TITLES[status],
        ]),
      }
    case "git":
      return {
        title: "Git",
        key: "gitStates",
        options: GIT_STATES.map((state) => [state, GIT_STATE_TITLES[state]]),
      }
    case "source":
      return {
        title: "Source",
        key: "sources",
        options: store.allSources.map((source) => [
          source,
          sourceTitle(source),
        ]),
      }
    default:
      return {
        title: "Repo",
        key: "repos",
        options: store.workspaces.map((workspace) => [
          workspace.key,
          workspace.name,
        ]),
      }
  }
}

/** A multi-select filter, where selecting nothing means everything. */
export default function FilterScreen() {
  const { filter } = useLocalSearchParams<{ filter: string }>()
  const store = useAgentStore()
  const { title, key, options } = filterSpec(filter, store)
  const selection = store.preferences[key] as string[]
  const set = (next: string[]) =>
    store.setPreferences({
      ...store.preferences,
      [key]: next.length === options.length ? [] : next,
    } as ListPreferences)
  const toggle = (value: string) =>
    set(
      selection.includes(value)
        ? selection.filter((item) => item !== value)
        : [...selection, value]
    )
  return (
    <>
      <Stack.Screen options={{ title }} />
      <ScrollView contentInsetAdjustmentBehavior="automatic">
        <CheckRow
          title="All"
          checked={selection.length === 0}
          onPress={() => set([])}
        />
        {options.map(([value, name]) => (
          <CheckRow
            key={value}
            title={name}
            checked={selection.includes(value)}
            onPress={() => toggle(value)}
          />
        ))}
      </ScrollView>
    </>
  )
}

function CheckRow({
  title,
  checked,
  onPress,
}: {
  title: string
  checked: boolean
  onPress: () => void
}) {
  return (
    <>
      <Pressable
        onPress={onPress}
        style={({ pressed }) => [
          styles.row,
          pressed && { backgroundColor: Theme.surfaceRaised },
        ]}
      >
        <Text style={styles.title}>{title}</Text>
        {checked ? (
          <Symbol
            name="checkmark"
            size={15}
            color={Theme.brand}
            weight="semibold"
          />
        ) : null}
      </Pressable>
      <Separator inset={20} />
    </>
  )
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    minHeight: 48,
    paddingHorizontal: 20,
  },
  title: { fontSize: 17, color: Theme.textPrimary },
})
