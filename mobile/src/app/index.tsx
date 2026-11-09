import { useImage } from "expo-image"
import { router, Stack } from "expo-router"
import type { ReactNode } from "react"
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native"

import {
  AllAgentsGlyph,
  Card,
  Separator,
  Symbol,
  WorkingGlyph,
} from "../design/components"
import { Theme } from "../design/theme"
import { listHref } from "../navigation/routes"
import { useAgentStore, useAppModel } from "../state/appModel"

export default function InboxScreen() {
  const model = useAppModel()
  const store = useAgentStore()
  const avatarURL = store.identity?.avatarURL
  // With no avatar the menu shows an icon instead; an empty source would fail to load.
  const avatar = useImage(
    avatarURL ?? "",
    { maxWidth: 96, onError: () => {} },
    [avatarURL]
  )
  const workspaces = store.workspaces
  return (
    <>
      <Stack.Toolbar placement="left">
        <Stack.Toolbar.Menu
          icon={avatarURL && avatar ? undefined : "person.crop.circle"}
          image={avatarURL ? avatar : null}
          iconRenderingMode="original"
          title={
            store.identity
              ? (store.identity.email ?? store.identity.login)
              : undefined
          }
          accessibilityLabel="Account"
        >
          {model.isDemo ? (
            <Stack.Toolbar.MenuAction
              icon="rectangle.portrait.and.arrow.right"
              onPress={() => model.signOut()}
            >
              Leave Demo
            </Stack.Toolbar.MenuAction>
          ) : (
            <Stack.Toolbar.MenuAction
              icon="rectangle.portrait.and.arrow.right"
              destructive
              onPress={() => model.signOut()}
            >
              Sign Out
            </Stack.Toolbar.MenuAction>
          )}
        </Stack.Toolbar.Menu>
      </Stack.Toolbar>
      <Stack.Toolbar placement="right">
        <Stack.Toolbar.Button
          icon="magnifyingglass"
          accessibilityLabel="Search"
          onPress={() => router.push(listHref({ scope: "search" }))}
        />
        <Stack.Toolbar.Button
          icon="square.and.pencil"
          accessibilityLabel="New Agent"
          onPress={() => router.push("/new-agent")}
        />
      </Stack.Toolbar>
      <ScrollView
        contentInsetAdjustmentBehavior="automatic"
        style={{ backgroundColor: Theme.background }}
        contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 40 }}
        refreshControl={
          <RefreshControl
            refreshing={false}
            onRefresh={() => store.refresh()}
          />
        }
      >
        <View style={styles.grid}>
          <CategoryCard
            title="All Agents"
            icon={<AllAgentsGlyph />}
            onPress={() => router.push(listHref({ scope: "all" }))}
          />
          <CategoryCard
            title="Needs Attention"
            count={store.count("needsAttention")}
            icon={
              <Symbol name="bell.badge" size={22} color={Theme.attention} />
            }
            onPress={() =>
              router.push(
                listHref({ scope: "category", category: "needsAttention" })
              )
            }
          />
          <CategoryCard
            title="Working"
            count={store.count("working")}
            icon={<WorkingGlyph />}
            onPress={() =>
              router.push(listHref({ scope: "category", category: "working" }))
            }
          />
          <CategoryCard
            title="In Review"
            count={store.count("inReview")}
            icon={
              <Symbol
                name="checkmark.circle.badge.questionmark"
                size={22}
                color={Theme.review}
              />
            }
            onPress={() =>
              router.push(listHref({ scope: "category", category: "inReview" }))
            }
          />
        </View>
        {workspaces.length > 0 ? (
          <>
            <Text style={styles.sectionHeader}>Workspaces</Text>
            {workspaces.map((workspace, index) => (
              <View key={workspace.key}>
                <Pressable
                  onPress={() =>
                    router.push(
                      listHref({
                        scope: "workspace",
                        key: workspace.key,
                        name: workspace.name,
                      })
                    )
                  }
                  style={({ pressed }) => [
                    styles.workspaceRow,
                    { opacity: pressed ? 0.6 : 1 },
                  ]}
                >
                  <View style={{ width: 30, alignItems: "center" }}>
                    <Symbol
                      name={workspace.key ? "folder" : "house"}
                      size={19}
                    />
                  </View>
                  <Text style={styles.workspaceName} numberOfLines={1}>
                    {workspace.name}
                  </Text>
                  <Text style={styles.workspaceCount}>{workspace.count}</Text>
                  <Symbol
                    name="chevron.right"
                    size={14}
                    color={Theme.textTertiary}
                    weight="semibold"
                  />
                </Pressable>
                {index < workspaces.length - 1 ? (
                  <Separator inset={52} />
                ) : null}
              </View>
            ))}
          </>
        ) : !store.hasLoaded ? (
          <ActivityIndicator style={{ marginTop: 60 }} />
        ) : null}
      </ScrollView>
    </>
  )
}

function CategoryCard({
  title,
  count,
  icon,
  onPress,
}: {
  title: string
  count?: number
  icon: ReactNode
  onPress: () => void
}) {
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.cardCell, { opacity: pressed ? 0.7 : 1 }]}
    >
      <Card style={styles.categoryCard}>
        <View style={{ height: 30, justifyContent: "center" }}>{icon}</View>
        <View style={{ flexDirection: "row", gap: 8, alignItems: "baseline" }}>
          <Text
            style={styles.cardTitle}
            numberOfLines={1}
            adjustsFontSizeToFit
            minimumFontScale={0.8}
          >
            {title}
          </Text>
          {count !== undefined ? (
            <Text style={styles.cardCount}>{count}</Text>
          ) : null}
        </View>
      </Card>
    </Pressable>
  )
}

const styles = StyleSheet.create({
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 14, marginTop: 12 },
  cardCell: { width: "47.5%", flexGrow: 1 },
  categoryCard: {
    minHeight: 112,
    padding: 14,
    justifyContent: "flex-end",
    gap: 10,
  },
  cardTitle: { fontSize: 17, color: Theme.textPrimary, flexShrink: 1 },
  cardCount: {
    fontSize: 17,
    color: Theme.textTertiary,
    fontVariant: ["tabular-nums"],
  },
  sectionHeader: {
    fontSize: 15,
    color: Theme.textSecondary,
    marginTop: 32,
    marginBottom: 6,
    marginHorizontal: 4,
  },
  workspaceRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    height: 58,
    paddingHorizontal: 4,
  },
  workspaceName: { flex: 1, fontSize: 17, color: Theme.textPrimary },
  workspaceCount: {
    fontSize: 17,
    color: Theme.textSecondary,
    fontVariant: ["tabular-nums"],
  },
})
