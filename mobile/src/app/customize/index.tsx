import { Host, Picker, Text as SwiftText, Toggle } from "@expo/ui/swift-ui"
import {
  labelsHidden,
  pickerStyle,
  tag,
  tint,
} from "@expo/ui/swift-ui/modifiers"
import { router, Stack } from "expo-router"
import type { SFSymbol } from "expo-symbols"
import type { ReactNode } from "react"
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native"

import {
  AGENT_STATUSES,
  GIT_STATE_TITLES,
  GIT_STATES,
  sourceTitle,
  STATUS_TITLES,
} from "../../core/models"
import { Separator, Symbol } from "../../design/components"
import { Palette, Theme } from "../../design/theme"
import { useAgentStore } from "../../state/appModel"
import {
  DEFAULT_PREFERENCES,
  GROUPING_TITLES,
  isDefaultPreferences,
  type Grouping,
  type ListPreferences,
} from "../../state/listPreferences"

/** "All", one name, or "First +2". */
function filterSummary(names: string[]): string {
  if (names.length === 0) return "All"
  return names.length === 1 ? names[0] : `${names[0]} +${names.length - 1}`
}

/** Grouping, filters and row properties for agent lists. */
export default function CustomizeScreen() {
  const store = useAgentStore()
  const preferences = store.preferences
  const update = (patch: Partial<ListPreferences>) =>
    store.setPreferences({ ...preferences, ...patch })
  const workspaceName = (key: string) =>
    store.workspaces.find((workspace) => workspace.key === key)?.name ?? key
  return (
    <>
      <Stack.Screen options={{ title: "Customize" }} />
      <Stack.Toolbar placement="left">
        <Stack.Toolbar.Button
          icon="xmark"
          accessibilityLabel="Close"
          onPress={() => router.back()}
        />
      </Stack.Toolbar>
      {!isDefaultPreferences(preferences) ? (
        <Stack.Toolbar placement="right">
          <Stack.Toolbar.Button
            onPress={() => store.setPreferences(DEFAULT_PREFERENCES)}
          >
            Reset
          </Stack.Toolbar.Button>
        </Stack.Toolbar>
      ) : null}
      <ScrollView
        contentInsetAdjustmentBehavior="automatic"
        contentContainerStyle={{ paddingBottom: 40 }}
      >
        <SectionHeader title="Grouping" />
        <Row>
          <PropertyLabel title="Group by" symbol="square.3.layers.3d" />
          <Host matchContents>
            <Picker
              selection={preferences.grouping}
              onSelectionChange={(grouping: Grouping) => update({ grouping })}
              modifiers={[
                pickerStyle("menu"),
                labelsHidden(),
                tint({ type: "hierarchical", style: "secondary" }),
              ]}
            >
              {(Object.keys(GROUPING_TITLES) as Grouping[]).map((grouping) => (
                <SwiftText key={grouping} modifiers={[tag(grouping)]}>
                  {GROUPING_TITLES[grouping]}
                </SwiftText>
              ))}
            </Picker>
          </Host>
        </Row>

        <SectionHeader title="Show" />
        <FilterRow
          title="Repo"
          symbol="folder"
          summary={filterSummary(
            [...preferences.repos].map(workspaceName).sort()
          )}
          filter="repo"
        />
        <Separator inset={62} />
        <FilterRow
          title="Status"
          symbol="rays"
          summary={filterSummary(
            AGENT_STATUSES.filter((status) =>
              preferences.statuses.includes(status)
            ).map((status) => STATUS_TITLES[status])
          )}
          filter="status"
        />
        <Separator inset={62} />
        <FilterRow
          title="Git"
          symbol="arrow.triangle.branch"
          summary={filterSummary(
            GIT_STATES.filter((state) =>
              preferences.gitStates.includes(state)
            ).map((state) => GIT_STATE_TITLES[state])
          )}
          filter="git"
        />
        <Separator inset={62} />
        <FilterRow
          title="Source"
          symbol="icloud.and.arrow.up"
          summary={filterSummary(preferences.sources.map(sourceTitle).sort())}
          filter="source"
        />
        <Separator inset={62} />

        <SectionHeader title="Agent Properties" />
        <ToggleRow
          title="Changes"
          symbol="plusminus"
          value={preferences.showChanges}
          onChange={(showChanges) => update({ showChanges })}
        />
        <Separator inset={62} />
        <ToggleRow
          title="Branch Status"
          symbol="arrow.triangle.pull"
          value={preferences.showBranchStatus}
          onChange={(showBranchStatus) => update({ showBranchStatus })}
        />
        <Separator inset={62} />
        <ToggleRow
          title="Runtime"
          symbol="desktopcomputer"
          value={preferences.showRuntime}
          onChange={(showRuntime) => update({ showRuntime })}
        />
        <Separator inset={62} />
        <ToggleRow
          title="Last Updated"
          symbol="clock"
          value={preferences.showLastUpdated}
          onChange={(showLastUpdated) => update({ showLastUpdated })}
        />
      </ScrollView>
    </>
  )
}

function SectionHeader({ title }: { title: string }) {
  return <Text style={styles.sectionHeader}>{title}</Text>
}

function Row({ children }: { children: ReactNode }) {
  return <View style={styles.row}>{children}</View>
}

function PropertyLabel({ title, symbol }: { title: string; symbol: SFSymbol }) {
  return (
    <View style={styles.label}>
      <View style={{ width: 28, alignItems: "center" }}>
        <Symbol name={symbol} size={18} />
      </View>
      <Text style={styles.labelText}>{title}</Text>
    </View>
  )
}

function FilterRow({
  title,
  symbol,
  summary,
  filter,
}: {
  title: string
  symbol: SFSymbol
  summary: string
  filter: string
}) {
  return (
    <Pressable
      onPress={() =>
        router.push({ pathname: "/customize/[filter]", params: { filter } })
      }
      style={({ pressed }) => [
        styles.row,
        pressed && { backgroundColor: Theme.surfaceRaised },
      ]}
    >
      <PropertyLabel title={title} symbol={symbol} />
      <Text style={styles.summary} numberOfLines={1}>
        {summary}
      </Text>
      <Symbol
        name="chevron.right"
        size={13}
        color={Theme.textTertiary}
        weight="semibold"
      />
    </Pressable>
  )
}

function ToggleRow({
  title,
  symbol,
  value,
  onChange,
}: {
  title: string
  symbol: SFSymbol
  value: boolean
  onChange: (value: boolean) => void
}) {
  return (
    <Row>
      <PropertyLabel title={title} symbol={symbol} />
      {/* SwiftUI's toggle: React Native's Switch keeps a pre-iOS 26 frame and draws off-centre. */}
      <Host matchContents>
        <Toggle
          isOn={value}
          onIsOnChange={onChange}
          label={title}
          modifiers={[labelsHidden(), tint(Palette.unread)]}
        />
      </Host>
    </Row>
  )
}

const styles = StyleSheet.create({
  sectionHeader: {
    fontSize: 16,
    color: Theme.textSecondary,
    paddingHorizontal: 20,
    paddingTop: 20,
    paddingBottom: 6,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    minHeight: 52,
    paddingHorizontal: 20,
  },
  label: { flex: 1, flexDirection: "row", alignItems: "center", gap: 14 },
  labelText: { fontSize: 17, color: Theme.textPrimary },
  summary: { fontSize: 17, color: Theme.textSecondary, maxWidth: 160 },
})
