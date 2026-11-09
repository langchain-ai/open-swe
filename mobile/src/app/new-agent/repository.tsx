import { router, Stack } from "expo-router"
import { useState } from "react"
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
} from "react-native"

import { Separator, Symbol } from "../../design/components"
import { Theme } from "../../design/theme"
import { useNewAgentDraft } from "../../features/newAgentDraft"

export default function RepositoryPicker() {
  const draft = useNewAgentDraft()
  const [query, setQuery] = useState("")
  const needle = query.trim().toLowerCase()
  const matches = (draft.repositories ?? []).filter(
    (name) => !needle || name.toLowerCase().includes(needle)
  )
  const choose = (repo: string | null) => {
    draft.set({ repo })
    router.back()
  }
  return (
    <>
      <Stack.Screen
        options={{
          title: "Repository",
          headerSearchBarOptions: {
            placeholder: "Search repositories",
            hideWhenScrolling: false,
            onChangeText: (event) => setQuery(event.nativeEvent.text),
          },
        }}
      />
      <ScrollView
        contentInsetAdjustmentBehavior="automatic"
        keyboardDismissMode="on-drag"
      >
        {!needle ? (
          <Row
            title="No Repo"
            checked={draft.repo === null}
            onPress={() => choose(null)}
          />
        ) : null}
        {matches.map((name) => (
          <Row
            key={name}
            title={name}
            checked={draft.repo === name}
            onPress={() => choose(name)}
          />
        ))}
        {draft.repositories === null ? (
          <ActivityIndicator style={{ marginTop: 20 }} />
        ) : draft.repositoriesError ? (
          <Text style={styles.error}>{draft.repositoriesError}</Text>
        ) : null}
      </ScrollView>
    </>
  )
}

function Row({
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
        <Text style={styles.title} numberOfLines={1}>
          {title}
        </Text>
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
    gap: 12,
    minHeight: 48,
    paddingHorizontal: 20,
  },
  title: { flex: 1, fontSize: 17, color: Theme.textPrimary },
  error: { fontSize: 13, color: Theme.textSecondary, padding: 20 },
})
