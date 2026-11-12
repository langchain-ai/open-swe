import { router, useLocalSearchParams } from "expo-router"
import {
  Linking,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native"

import { isLocal, sourceTitle, STATUS_TITLES } from "../../core/models"
import { Separator, Symbol } from "../../design/components"
import { Theme } from "../../design/theme"
import { useAgentStore } from "../../state/appModel"

const formatDate = (date: Date) =>
  date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })

export default function ThreadDetailsSheet() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const thread = useAgentStore().thread(id)
  if (!thread) return null
  const rows: [string, string, boolean?][] = [
    ["Status", STATUS_TITLES[thread.status]],
    ...(thread.repoFullName
      ? [["Repository", thread.repoFullName] as [string, string]]
      : []),
    ...(thread.branch
      ? [["Branch", thread.branch, true] as [string, string, boolean]]
      : []),
    ...(thread.model ? [["Model", thread.model] as [string, string]] : []),
    ["Source", sourceTitle(thread.source)],
    ["Runtime", isLocal(thread) ? "Local machine" : "Cloud sandbox"],
    ...(thread.ownerLogin
      ? [["Owner", thread.ownerLogin] as [string, string]]
      : []),
    ["Created", formatDate(thread.createdAt)],
    ["Updated", formatDate(thread.updatedAt)],
  ]
  return (
    <ScrollView
      // The header is the first, sticky child: a sibling above the
      // ScrollView gets drawn over by the form sheet's content. The sticky
      // wrapper takes over its child's style, hence the plain outer View.
      stickyHeaderIndices={[0]}
      contentInsetAdjustmentBehavior="never"
      style={{ flex: 1, backgroundColor: Theme.surface }}
    >
      <View>
        <View style={styles.header}>
          <Pressable
            onPress={() => router.back()}
            style={styles.close}
            accessibilityLabel="Close"
          >
            <Symbol
              name="xmark"
              size={16}
              color={Theme.textPrimary}
              weight="semibold"
            />
          </Pressable>
          <Text style={styles.title}>Details</Text>
          <View style={{ width: 44 }} />
        </View>
      </View>
      <View style={{ paddingHorizontal: 20, paddingBottom: 40 }}>
        {rows.map(([label, value, mono], index) => (
          <View key={label}>
            <View style={styles.row}>
              <Text style={styles.label}>{label}</Text>
              <Text
                style={[
                  styles.value,
                  mono && { fontFamily: "Menlo", fontSize: 14 },
                ]}
                selectable
              >
                {value}
              </Text>
            </View>
            {index < rows.length - 1 || thread.pr ? <Separator /> : null}
          </View>
        ))}
        {thread.pr ? (
          <Pressable
            onPress={() => Linking.openURL(thread.pr!.url)}
            style={styles.row}
          >
            <Text style={styles.label}>Pull Request</Text>
            <View
              style={{ flexDirection: "row", alignItems: "center", gap: 6 }}
            >
              <Text style={[styles.value, { color: Theme.link }]}>
                #{thread.pr.number}
              </Text>
              <Symbol name="arrow.up.right" size={13} color={Theme.link} />
            </View>
          </Pressable>
        ) : null}
      </View>
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingTop: 16,
    paddingBottom: 8,
    backgroundColor: Theme.surface,
  },
  close: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: Theme.surfaceRaised,
    alignItems: "center",
    justifyContent: "center",
  },
  title: { fontSize: 17, fontWeight: "600", color: Theme.textPrimary },
  row: {
    flexDirection: "row",
    alignItems: "baseline",
    justifyContent: "space-between",
    gap: 16,
    paddingVertical: 13,
  },
  label: { fontSize: 17, color: Theme.textSecondary },
  value: {
    fontSize: 17,
    color: Theme.textPrimary,
    flexShrink: 1,
    textAlign: "right",
  },
})
