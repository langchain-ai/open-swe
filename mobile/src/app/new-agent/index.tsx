import { Host, Picker, Text as SwiftText } from "@expo/ui/swift-ui"
import {
  labelsHidden,
  pickerStyle,
  tag,
  tint,
} from "@expo/ui/swift-ui/modifiers"
import { router, Stack } from "expo-router"
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native"

import {
  VISIBILITY_TITLES,
  type AgentVisibility,
} from "../../core/agentService"
import { Card, Separator, Symbol } from "../../design/components"
import { Theme } from "../../design/theme"
import { useNewAgentDraft } from "../../features/newAgentDraft"
import { threadHref } from "../../navigation/routes"

/** Start an agent: a prompt, the repository it works in, and who can see it. */
export default function NewAgentScreen() {
  const draft = useNewAgentDraft()
  const start = async () => {
    const id = await draft.start()
    if (!id) return
    router.dismiss()
    router.push(threadHref(id))
  }
  return (
    <>
      <Stack.Screen
        options={{ title: "New Agent", gestureEnabled: !draft.isStarting }}
      />
      <Stack.Toolbar placement="left">
        <Stack.Toolbar.Button
          icon="xmark"
          accessibilityLabel="Close"
          onPress={() => router.back()}
        />
      </Stack.Toolbar>
      <Stack.Toolbar placement="right">
        <Stack.Toolbar.Button
          icon="arrow.up"
          variant="prominent"
          accessibilityLabel="Start"
          disabled={!draft.canStart}
          onPress={start}
        />
      </Stack.Toolbar>
      <ScrollView
        contentInsetAdjustmentBehavior="automatic"
        keyboardDismissMode="interactive"
        contentContainerStyle={{ padding: 16, gap: 18 }}
      >
        <Card radius={18}>
          <TextInput
            value={draft.prompt}
            onChangeText={(prompt) => draft.set({ prompt })}
            placeholder="What should the agent do?"
            placeholderTextColor={Theme.textTertiary}
            multiline
            autoFocus
            style={styles.prompt}
          />
        </Card>
        <Card radius={18}>
          <Pressable
            onPress={() => router.push("/new-agent/repository")}
            style={({ pressed }) => [
              styles.option,
              pressed && { backgroundColor: Theme.surfaceRaised },
            ]}
          >
            <OptionLabel title="Repository" symbol="folder" />
            <Text style={styles.value} numberOfLines={1} ellipsizeMode="middle">
              {draft.repo ?? "No Repo"}
            </Text>
            <Symbol
              name="chevron.right"
              size={13}
              color={Theme.textTertiary}
              weight="semibold"
            />
          </Pressable>
          <View style={{ marginLeft: 56 }}>
            <Separator />
          </View>
          <View style={styles.option}>
            <OptionLabel
              title="Visibility"
              symbol={draft.visibility === "private" ? "lock" : "person.2"}
            />
            <Host matchContents>
              <Picker
                selection={draft.visibility}
                onSelectionChange={(visibility: AgentVisibility) =>
                  draft.set({ visibility })
                }
                modifiers={[
                  pickerStyle("menu"),
                  labelsHidden(),
                  tint({ type: "hierarchical", style: "secondary" }),
                ]}
              >
                {(Object.keys(VISIBILITY_TITLES) as AgentVisibility[]).map(
                  (visibility) => (
                    <SwiftText key={visibility} modifiers={[tag(visibility)]}>
                      {VISIBILITY_TITLES[visibility]}
                    </SwiftText>
                  )
                )}
              </Picker>
            </Host>
          </View>
        </Card>
        {draft.error ? <Text style={styles.error}>{draft.error}</Text> : null}
      </ScrollView>
    </>
  )
}

function OptionLabel({
  title,
  symbol,
}: {
  title: string
  symbol: "folder" | "lock" | "person.2"
}) {
  return (
    <View
      style={{ flex: 1, flexDirection: "row", alignItems: "center", gap: 14 }}
    >
      <View style={{ width: 26, alignItems: "center" }}>
        <Symbol name={symbol} size={18} />
      </View>
      <Text style={{ fontSize: 17, color: Theme.textPrimary }}>{title}</Text>
    </View>
  )
}

const styles = StyleSheet.create({
  prompt: {
    minHeight: 150,
    maxHeight: 320,
    padding: 16,
    paddingTop: 16,
    fontSize: 17,
    color: Theme.textPrimary,
    textAlignVertical: "top",
  },
  option: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    minHeight: 54,
    paddingHorizontal: 16,
  },
  value: { fontSize: 17, color: Theme.textSecondary, maxWidth: 190 },
  error: { fontSize: 13, color: Theme.error, marginHorizontal: 4 },
})
