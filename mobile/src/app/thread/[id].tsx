import { router, Stack, useLocalSearchParams } from "expo-router"
import { useRef, useState } from "react"
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native"
import { useSafeAreaInsets } from "react-native-safe-area-context"

import type { TranscriptItem } from "../../core/transcript"
import { Symbol } from "../../design/components"
import { Theme } from "../../design/theme"
import { ThreadToolbarMenu } from "../../features/threadActions"
import {
  ChangesCard,
  TranscriptItemView,
  WorkingIndicator,
} from "../../features/TranscriptItemView"
import { useThreadFollower } from "../../features/useThreadFollower"
import { changesHref, detailsHref } from "../../navigation/routes"
import { useAgentStore } from "../../state/appModel"

export default function ThreadScreen() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const store = useAgentStore()
  const thread = store.thread(id)
  const follower = useThreadFollower(store, id)
  const { log, items, diff } = follower
  const insets = useSafeAreaInsets()
  const list = useRef<FlatList<TranscriptItem>>(null)
  const nearBottom = useRef(true)
  // Opens at the latest message, like the Swift app's bottom scroll anchor.
  const anchored = useRef(false)
  const [draft, setDraft] = useState("")
  const [sending, setSending] = useState(false)
  const [composerFocused, setComposerFocused] = useState(false)

  /**
   * The thread's status decides, as the dashboard's does, including while a
   * requested run waits to start. The log only overrules it with evidence: a
   * turn seen starting live counts until the stream closes it, and a newest
   * turn that has ended means a status still saying running is stale.
   */
  const isRunning = log
    ? log.hasLiveActiveTurn ||
      (thread?.status === "running" && !log.latestTurnEnded)
    : thread?.status === "running"

  /** Why the last run did not finish, when it did not. */
  const runOutcome =
    log?.failure ??
    (thread?.status === "error"
      ? "The last run failed."
      : thread?.status === "interrupted"
        ? "The last run was stopped."
        : null)

  const canSend = !sending && draft.trim().length > 0
  const send = async () => {
    const text = draft.trim()
    if (!text) return
    setSending(true)
    try {
      await store.service.sendMessage(id, text, isRunning)
      setDraft("")
      nearBottom.current = true
      await follower.afterSend()
    } catch (error) {
      store.report(error)
    } finally {
      setSending(false)
    }
  }

  const footer = (
    <View style={{ gap: 18, paddingTop: 18 }}>
      {isRunning ? (
        <WorkingIndicator />
      ) : runOutcome ? (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <Symbol
            name="exclamationmark.triangle"
            size={15}
            color={Theme.error}
          />
          <Text style={{ fontSize: 15, color: Theme.error, flex: 1 }}>
            {runOutcome}
          </Text>
        </View>
      ) : null}
      {diff && diff.files.length > 0 ? (
        <ChangesCard
          files={diff.files}
          onPress={() => router.push(changesHref(id))}
        />
      ) : null}
    </View>
  )

  const header = log?.olderCursor ? (
    // A new identity per cursor, so the row loads again for each older page.
    <View key={log.olderCursor} style={styles.older}>
      {follower.olderFailed ? (
        <Pressable onPress={follower.loadOlder}>
          <Text style={{ fontSize: 15, color: Theme.textSecondary }}>
            Load earlier messages
          </Text>
        </Pressable>
      ) : (
        <>
          <ActivityIndicator size="small" />
          <Text style={{ fontSize: 15, color: Theme.textTertiary }}>
            Loading earlier messages
          </Text>
        </>
      )}
    </View>
  ) : undefined

  return (
    <>
      <Stack.Screen options={{ title: thread?.title ?? "Agent" }} />
      {thread ? (
        <ThreadToolbarMenu
          store={store}
          thread={thread}
          showDetails={() => router.push(detailsHref(id))}
        />
      ) : null}
      <KeyboardAvoidingView
        behavior="padding"
        style={{ flex: 1, backgroundColor: Theme.background }}
      >
        {items === null ? (
          <ActivityIndicator style={{ marginTop: 160 }} />
        ) : (
          <FlatList
            ref={list}
            data={items}
            keyExtractor={(item) => item.id}
            contentInsetAdjustmentBehavior="automatic"
            keyboardDismissMode="interactive"
            contentContainerStyle={{
              paddingHorizontal: 16,
              paddingTop: 8,
              paddingBottom: 24,
            }}
            ItemSeparatorComponent={() => <View style={{ height: 18 }} />}
            renderItem={({ item }) => <TranscriptItemView item={item} />}
            ListHeaderComponent={header}
            ListFooterComponent={footer}
            // Earlier turns load above the reader without moving them.
            maintainVisibleContentPosition={{ minIndexForVisible: 0 }}
            onStartReached={follower.loadOlder}
            onStartReachedThreshold={0.5}
            onScroll={({ nativeEvent }) => {
              if (!anchored.current) return
              const { contentOffset, contentSize, layoutMeasurement } =
                nativeEvent
              nearBottom.current =
                contentOffset.y + layoutMeasurement.height >
                contentSize.height - 120
            }}
            scrollEventThrottle={64}
            // Follow what arrives at the end, unless the reader scrolled up,
            // including when the keyboard or a sheet resizes the list.
            onContentSizeChange={() => {
              if (nearBottom.current)
                list.current?.scrollToEnd({ animated: false })
            }}
            onLayout={() => {
              if (nearBottom.current)
                list.current?.scrollToEnd({ animated: false })
            }}
            onMomentumScrollBegin={() => (anchored.current = true)}
            onScrollBeginDrag={() => (anchored.current = true)}
          />
        )}
        <View
          style={[
            styles.composerWrap,
            { paddingBottom: composerFocused ? 6 : Math.max(6, insets.bottom) },
          ]}
        >
          <View style={styles.composer}>
            <TextInput
              value={draft}
              onChangeText={setDraft}
              placeholder={isRunning ? "Queue a follow-up" : "Add a follow-up"}
              placeholderTextColor={Theme.textTertiary}
              multiline
              onFocus={() => setComposerFocused(true)}
              onBlur={() => setComposerFocused(false)}
              style={styles.input}
            />
            <Pressable
              onPress={send}
              disabled={!canSend}
              style={[
                styles.send,
                {
                  backgroundColor:
                    canSend || sending ? Theme.brand : Theme.surfaceRaised,
                },
              ]}
              accessibilityLabel="Send"
            >
              {sending ? (
                <ActivityIndicator color={Theme.onBrand} />
              ) : (
                <Symbol
                  name="arrow.up"
                  size={16}
                  weight="bold"
                  color={canSend ? Theme.onBrand : Theme.textTertiary}
                />
              )}
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>
    </>
  )
}

const styles = StyleSheet.create({
  older: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    paddingVertical: 8,
    marginBottom: 18,
  },
  composerWrap: {
    paddingHorizontal: 12,
    paddingTop: 6,
    backgroundColor: Theme.background,
  },
  composer: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 10,
    paddingLeft: 18,
    paddingRight: 8,
    backgroundColor: Theme.surface,
    borderColor: Theme.stroke,
    borderWidth: StyleSheet.hairlineWidth * 2,
    borderRadius: 24,
  },
  input: {
    flex: 1,
    fontSize: 17,
    color: Theme.textPrimary,
    paddingTop: 13,
    paddingBottom: 13,
    maxHeight: 160,
  },
  send: {
    width: 34,
    height: 34,
    borderRadius: 17,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 7,
  },
})
