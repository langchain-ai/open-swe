import { Button, Host, Menu } from "@expo/ui/swift-ui"
import { labelStyle, tint } from "@expo/ui/swift-ui/modifiers"
import * as Clipboard from "expo-clipboard"
import { Stack, useLocalSearchParams } from "expo-router"
import type { SFSymbol } from "expo-symbols"
import { useEffect, useState } from "react"
import {
  ActivityIndicator,
  LayoutAnimation,
  Linking,
  Pressable,
  ScrollView,
  Share,
  StyleSheet,
  Text,
  View,
  type ColorValue,
} from "react-native"

import { APIError } from "../../core/apiClient"
import { diffLines, type DiffLine } from "../../core/diffLines"
import {
  diffTotals,
  fileDirectory,
  fileName,
  type BranchDiff,
  type DiffFile,
  type PullRequestHealth,
  type PullRequestState,
} from "../../core/models"
import { Card, ChangeCounts, FileIcon, Symbol } from "../../design/components"
import { pullRequestBadge, Theme } from "../../design/theme"
import { useAgentStore } from "../../state/appModel"

type Tab = "Overview" | "Discussion" | "Commits"
const TABS: Tab[] = ["Overview", "Discussion", "Commits"]

/** A thread's pull request, or its branch changes when it has none. */
export default function ChangesScreen() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const store = useAgentStore()
  const thread = store.thread(id)
  const pr = thread?.pr
  const [diff, setDiff] = useState<BranchDiff | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [health, setHealth] = useState<PullRequestHealth | null>(null)
  const [healthLoaded, setHealthLoaded] = useState(false)
  const [tab, setTab] = useState<Tab>("Overview")
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const prURL = pr?.url
  useEffect(() => {
    let cancelled = false
    const branchDiff = store.service.branchDiff(id)
    const tracked = store.thread(id)?.pr
    ;(async () => {
      if (tracked) {
        const value = await store.service
          .pullRequestHealth(id, tracked)
          .catch(() => null)
        if (!cancelled) setHealth(value)
      }
      if (!cancelled) setHealthLoaded(true)
      try {
        const value = await branchDiff
        if (cancelled) return
        setDiff(value)
        if (value.files.length === 1)
          setExpanded(new Set([value.files[0].path]))
      } catch (error) {
        if (!cancelled) setFailed(APIError.describe(error))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [store, id, prURL])

  const toggle = (path: string) => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut)
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  const totals = diff ? diffTotals(diff) : null
  const head = pr?.headRef || diff?.headRef || thread?.branch
  return (
    <>
      <Stack.Screen options={{ title: "" }} />
      {pr ? (
        <Stack.Toolbar placement="right">
          <Stack.Toolbar.Button
            icon="link"
            accessibilityLabel="Open on GitHub"
            onPress={() => Linking.openURL(pr.url)}
          />
          <Stack.Toolbar.Menu icon="ellipsis" accessibilityLabel="More">
            <Stack.Toolbar.MenuAction
              icon="safari"
              onPress={() => Linking.openURL(pr.url)}
            >
              Open on GitHub
            </Stack.Toolbar.MenuAction>
            <Stack.Toolbar.MenuAction
              icon="doc.on.doc"
              onPress={() => Clipboard.setStringAsync(pr.url)}
            >
              Copy URL
            </Stack.Toolbar.MenuAction>
            <Stack.Toolbar.MenuAction
              icon="arrow.triangle.branch"
              onPress={() => Clipboard.setStringAsync(pr.headRef)}
            >
              Copy Branch Name
            </Stack.Toolbar.MenuAction>
            <Stack.Toolbar.MenuAction
              icon="square.and.arrow.up"
              onPress={() => Share.share({ url: pr.url })}
            >
              Share
            </Stack.Toolbar.MenuAction>
          </Stack.Toolbar.Menu>
        </Stack.Toolbar>
      ) : null}
      <ScrollView
        contentInsetAdjustmentBehavior="automatic"
        style={{ backgroundColor: Theme.background }}
        contentContainerStyle={{ paddingTop: 8, paddingBottom: 40 }}
      >
        <View style={{ paddingHorizontal: 20, gap: 14 }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
            {pr ? <StateBadge state={pr.state} /> : null}
            {diff && totals ? (
              <View
                style={{ flexDirection: "row", alignItems: "center", gap: 6 }}
              >
                <ChangeCounts
                  additions={totals.additions}
                  deletions={totals.deletions}
                  size={17}
                />
                <Text style={{ fontSize: 17, color: Theme.textTertiary }}>
                  ·
                </Text>
                <Text style={{ fontSize: 17, color: Theme.textSecondary }}>
                  {diff.files.length}{" "}
                  {diff.files.length === 1 ? "File" : "Files"}
                </Text>
              </View>
            ) : null}
          </View>
          <Text style={styles.prTitle}>
            {pr?.title ?? thread?.title ?? "Changes"}
            {pr ? (
              <Text style={{ color: Theme.textTertiary }}> #{pr.number}</Text>
            ) : null}
          </Text>
          {head ? (
            <View
              style={{ flexDirection: "row", alignItems: "center", gap: 10 }}
            >
              <Text
                style={[styles.branch, { flexShrink: 1 }]}
                numberOfLines={1}
                ellipsizeMode="middle"
              >
                {head}
              </Text>
              <Symbol
                name="arrow.right"
                size={13}
                color={Theme.textSecondary}
              />
              <Text style={styles.branch} numberOfLines={1}>
                {pr?.baseRef || diff?.baseRef || "main"}
              </Text>
            </View>
          ) : null}
        </View>
        {pr ? (
          <View style={styles.tabs}>
            {TABS.map((option) => (
              <Pressable
                key={option}
                onPress={() => setTab(option)}
                style={[
                  styles.tab,
                  tab === option && { backgroundColor: Theme.surfaceRaised },
                ]}
              >
                <Text
                  style={{
                    fontSize: 17,
                    color:
                      tab === option ? Theme.textPrimary : Theme.textSecondary,
                  }}
                >
                  {option}
                </Text>
              </Pressable>
            ))}
          </View>
        ) : null}
        <View style={{ paddingTop: 18 }}>
          {tab === "Overview" ? (
            <>
              {pr ? (
                <View style={{ paddingHorizontal: 16, paddingBottom: 30 }}>
                  <ChecksCard
                    health={health}
                    loaded={healthLoaded}
                    baseRef={pr.baseRef}
                  />
                </View>
              ) : null}
              {failed ? (
                <Text style={styles.note}>{failed}</Text>
              ) : diff ? (
                diff.files.length === 0 ? (
                  <Text style={styles.note}>
                    No changes on this branch yet.
                  </Text>
                ) : (
                  <>
                    <View style={styles.filesHeader}>
                      <Text
                        style={{ fontSize: 17, color: Theme.textSecondary }}
                      >
                        {diff.files.length}{" "}
                        {diff.files.length === 1 ? "File" : "Files"}
                      </Text>
                      <Pressable
                        onPress={() => {
                          LayoutAnimation.configureNext(
                            LayoutAnimation.Presets.easeInEaseOut
                          )
                          setExpanded(
                            expanded.size === diff.files.length
                              ? new Set()
                              : new Set(diff.files.map((file) => file.path))
                          )
                        }}
                      >
                        <Text
                          style={{ fontSize: 17, color: Theme.textSecondary }}
                        >
                          {expanded.size === diff.files.length
                            ? "Collapse All"
                            : "Expand All"}
                        </Text>
                      </Pressable>
                    </View>
                    {diff.files.map((file) => (
                      <FileDiffSection
                        key={file.path}
                        file={file}
                        expanded={expanded.has(file.path)}
                        onToggle={() => toggle(file.path)}
                        pullRequestURL={pr?.url}
                      />
                    ))}
                    {diff.truncated ? (
                      <Text
                        style={[styles.note, { padding: 20, fontSize: 13 }]}
                      >
                        Some files are not shown.
                      </Text>
                    ) : null}
                  </>
                )
              ) : (
                <ActivityIndicator style={{ marginTop: 30 }} />
              )}
            </>
          ) : (
            <GitHubCard
              title={tab}
              detail={
                tab === "Discussion"
                  ? "Read and reply to review comments on GitHub."
                  : "Browse this branch's commits on GitHub."
              }
              url={pr ? pr.url + (tab === "Commits" ? "/commits" : "") : null}
            />
          )}
        </View>
      </ScrollView>
    </>
  )
}

function StateBadge({ state }: { state: PullRequestState }) {
  const { color, background } = pullRequestBadge(state)
  return (
    <View style={[styles.badge, { backgroundColor: background }]}>
      <Text style={{ fontSize: 16, fontWeight: "500", color }}>
        {state.charAt(0).toUpperCase() + state.slice(1)}
      </Text>
    </View>
  )
}

function ChecksCard({
  health,
  loaded,
  baseRef,
}: {
  health: PullRequestHealth | null
  loaded: boolean
  baseRef: string
}) {
  const [title, detail, symbol, color] = checksSummary(health, loaded, baseRef)
  return (
    <Card
      style={{
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 14,
        paddingHorizontal: 18,
        paddingVertical: 16,
      }}
    >
      {symbol ? (
        <View style={{ paddingTop: 2 }}>
          <Symbol name={symbol} size={20} color={color} />
        </View>
      ) : null}
      <View style={{ flex: 1, gap: 4 }}>
        <Text style={{ fontSize: 18, color: Theme.textPrimary }}>{title}</Text>
        <Text style={{ fontSize: 16, color: Theme.textSecondary }}>
          {detail}
        </Text>
      </View>
    </Card>
  )
}

function checksSummary(
  health: PullRequestHealth | null,
  loaded: boolean,
  baseRef: string
): [string, string, SFSymbol | null, ColorValue] {
  if (!loaded)
    return [
      "Loading",
      "Fetching checks, reviews, and deployments",
      null,
      Theme.textSecondary,
    ]
  if (!health || !health.checksAvailable) {
    return [
      "Checks unavailable",
      "GitHub did not report checks for this pull request.",
      "questionmark.circle",
      Theme.textSecondary,
    ]
  }
  if (health.mergeConflictState === "conflicting") {
    return [
      "Merge conflicts",
      `This branch conflicts with ${baseRef || "the base branch"}.`,
      "exclamationmark.triangle.fill",
      Theme.attention,
    ]
  }
  if (health.failingChecks.length > 0) {
    const count = health.failingChecks.length
    return [
      `${count} failing ${count === 1 ? "check" : "checks"}`,
      health.failingChecks
        .slice(0, 3)
        .map((check) => check.name)
        .join(", "),
      "xmark.circle.fill",
      Theme.error,
    ]
  }
  const pending = health.pendingCheckCount ?? 0
  if (pending > 0)
    return [
      "Checks running",
      `${pending} ${pending === 1 ? "check is" : "checks are"} still in progress.`,
      "clock",
      Theme.attention,
    ]
  const comments = health.unresolvedReviewThreadCount ?? 0
  return [
    "All checks passed",
    comments > 0
      ? `${comments} unresolved review ${comments === 1 ? "thread" : "threads"}`
      : "No unresolved review threads",
    "checkmark.circle.fill",
    Theme.success,
  ]
}

function GitHubCard({
  title,
  detail,
  url,
}: {
  title: string
  detail: string
  url: string | null
}) {
  return (
    <View style={{ paddingHorizontal: 16 }}>
      <Card style={{ padding: 18, gap: 14 }}>
        <Text style={{ fontSize: 18, color: Theme.textPrimary }}>{title}</Text>
        <Text style={{ fontSize: 16, color: Theme.textSecondary }}>
          {detail}
        </Text>
        {url ? (
          <Pressable
            onPress={() => Linking.openURL(url)}
            style={styles.githubButton}
          >
            <Text
              style={{ fontSize: 16, fontWeight: "500", color: Theme.onBrand }}
            >
              Open on GitHub
            </Text>
            <Symbol
              name="arrow.up.right"
              size={14}
              color={Theme.onBrand}
              weight="medium"
            />
          </Pressable>
        ) : null}
      </Card>
    </View>
  )
}

function FileDiffSection({
  file,
  expanded,
  onToggle,
  pullRequestURL,
}: {
  file: DiffFile
  expanded: boolean
  onToggle: () => void
  pullRequestURL?: string
}) {
  return (
    <View>
      <View style={styles.hairline} />
      <View style={styles.fileHeader}>
        <Pressable onPress={onToggle} style={styles.fileToggle}>
          <View
            style={{
              width: 16,
              transform: [{ rotate: expanded ? "90deg" : "0deg" }],
            }}
          >
            <Symbol name="chevron.right" size={14} weight="semibold" />
          </View>
          <FileIcon path={file.path} />
          <View
            style={{
              flex: 1,
              flexDirection: "row",
              alignItems: "baseline",
              gap: 8,
            }}
          >
            <Text
              style={{ fontSize: 17, color: Theme.textPrimary }}
              numberOfLines={1}
            >
              {fileName(file.path)}
            </Text>
            <Text
              style={{ fontSize: 17, color: Theme.textTertiary, flexShrink: 1 }}
              numberOfLines={1}
              ellipsizeMode="head"
            >
              {fileDirectory(file.path)}
            </Text>
          </View>
          <ChangeCounts
            additions={file.additions}
            deletions={file.deletions}
            size={16}
          />
        </Pressable>
        {/* A sibling of the toggle, so opening the menu leaves the file as it is. */}
        <FileMenu path={file.path} pullRequestURL={pullRequestURL} />
      </View>
      {expanded ? (
        <>
          <View style={styles.hairline} />
          <DiffBody file={file} />
        </>
      ) : null}
    </View>
  )
}

/** The row's `•••` menu: copy the path, or open the file on GitHub. */
function FileMenu({
  path,
  pullRequestURL,
}: {
  path: string
  pullRequestURL?: string
}) {
  return (
    <Host matchContents>
      <Menu
        label="More"
        systemImage="ellipsis"
        modifiers={[
          labelStyle("iconOnly"),
          tint({ type: "hierarchical", style: "secondary" }),
        ]}
      >
        <Button
          label="Copy Path"
          systemImage="doc.on.doc"
          onPress={() => Clipboard.setStringAsync(path)}
        />
        {pullRequestURL ? (
          <Button
            label="View on GitHub"
            systemImage="safari"
            onPress={() => Linking.openURL(`${pullRequestURL}/files`)}
          />
        ) : null}
      </Menu>
    </Host>
  )
}

/** The diff rows of one file, scrolling sideways as one block. */
function DiffBody({ file }: { file: DiffFile }) {
  const lines = diffLines(file)
  if (file.unrenderable || lines.length === 0) {
    return (
      <Text style={[styles.note, { padding: 16 }]}>
        {file.unrenderable ? "This file can't be shown." : "No line changes."}
      </Text>
    )
  }
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      style={{ backgroundColor: Theme.background }}
      contentContainerStyle={{ minWidth: "100%", paddingVertical: 4 }}
    >
      <View style={{ minWidth: "100%" }}>
        {lines.map((line, index) => (
          <DiffLineRow key={index} line={line} />
        ))}
      </View>
    </ScrollView>
  )
}

function DiffLineRow({ line }: { line: DiffLine }) {
  if (line.kind === "gap") {
    return (
      <View style={styles.gap}>
        <Text style={[styles.mono, { color: Theme.textSecondary }]}>
          {line.count} unmodified {line.count === 1 ? "line" : "lines"}
        </Text>
      </View>
    )
  }
  const [number, color, background] =
    line.kind === "added"
      ? [line.new, Theme.success, Theme.successBackground]
      : line.kind === "removed"
        ? [line.old, Theme.error, Theme.errorBackground]
        : [line.new, null, "transparent"]
  return (
    <View style={[styles.line, { backgroundColor: background as ColorValue }]}>
      <View
        style={{
          width: 3,
          alignSelf: "stretch",
          backgroundColor: color ?? "transparent",
        }}
      />
      <Text
        style={[
          styles.mono,
          styles.lineNumber,
          { color: color ?? Theme.textTertiary },
        ]}
      >
        {number}
      </Text>
      <Text
        style={[styles.mono, { color: Theme.textPrimary, paddingRight: 20 }]}
      >
        {line.text || " "}
      </Text>
    </View>
  )
}

const styles = StyleSheet.create({
  prTitle: { fontSize: 26, fontWeight: "600", color: Theme.textPrimary },
  branch: { fontSize: 15, color: Theme.textSecondary },
  tabs: { flexDirection: "row", marginHorizontal: 16, marginTop: 26 },
  tab: {
    flex: 1,
    height: 46,
    borderRadius: 23,
    alignItems: "center",
    justifyContent: "center",
  },
  badge: {
    paddingHorizontal: 14,
    height: 34,
    borderRadius: 17,
    justifyContent: "center",
  },
  note: { fontSize: 17, color: Theme.textSecondary, paddingHorizontal: 20 },
  filesHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: 20,
    paddingBottom: 14,
  },
  hairline: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: Theme.separator,
  },
  fileHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    height: 60,
    paddingLeft: 20,
    paddingRight: 12,
    backgroundColor: Theme.surface,
  },
  fileToggle: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    alignSelf: "stretch",
  },
  githubButton: {
    alignSelf: "flex-start",
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    height: 42,
    paddingHorizontal: 18,
    borderRadius: 21,
    backgroundColor: Theme.brand,
  },
  mono: { fontFamily: "Menlo", fontSize: 14 },
  gap: {
    minHeight: 32,
    justifyContent: "center",
    paddingLeft: 20,
    backgroundColor: Theme.surface,
  },
  line: { flexDirection: "row", alignItems: "center", minHeight: 22 },
  lineNumber: { width: 40, textAlign: "right", marginRight: 12 },
})
