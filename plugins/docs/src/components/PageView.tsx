import { Avatar } from "@langchain/macaw-components/Avatar"
import { Badge } from "@langchain/macaw-components/Badge"
import { Banner } from "@langchain/macaw-components/Banner"
import { Button } from "@langchain/macaw-components/Button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Spinner } from "@langchain/macaw-components/Spinner"
import { HostError } from "@openswe/plugin-sdk"
import { useHostContext, usePluginHost } from "@openswe/plugin-sdk/react"
import { ChatCircleTextIcon } from "@phosphor-icons/react/dist/ssr/ChatCircleText"
import { DotsThreeIcon } from "@phosphor-icons/react/dist/ssr/DotsThree"
import { SparkleIcon } from "@phosphor-icons/react/dist/ssr/Sparkle"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import type { Editor } from "@tiptap/react"
import type { Node as ProseMirrorNode } from "@tiptap/pm/model"
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react"

import {
  CommentsPanel,
  type CommentThread,
  type Composing,
} from "@/components/Comments"
import { PageEditor } from "@/components/Editor"
import { SideSheet } from "@/components/SideSheet"
import { useOpenPageTools, useSharedContext } from "@/lib/agent"
import type { ImageFile } from "@/lib/images"
import {
  anchorFor,
  headingBefore,
  resolveAnchor,
  type Highlight,
} from "@/lib/anchors"
import { errorMessage } from "@/lib/host"
import {
  asLiveMarkdown,
  useLiveDoc,
  usePresence,
  type LiveDoc,
} from "@/lib/live"
import { preserveFormatting } from "@/lib/preserve"
import {
  draftsIn,
  colorFor,
  draftBranch,
  draftRoom,
  pageTitle,
  resolveLink,
  splitFrontMatter,
  timeAgo,
  withTrailingNewline,
  type CommentStatus,
  type Draft,
  type PageComment,
} from "@/lib/model"
import {
  fileQuery,
  keys,
  useComments,
  useDrafts,
  useFile,
  useHead,
  useHistory,
  usePeople,
  usePull,
  usePutComment,
  useSpace,
} from "@/lib/queries"

type Tab = "comments" | "activity"

const STATUS_FAILURE: Record<CommentStatus, string> = {
  open: "Couldn't reopen the comment",
  resolved: "Couldn't resolve the comment",
  accepted: "Couldn't mark the suggestion accepted",
  rejected: "Couldn't reject the suggestion",
}

/** A passage the person selected, and the text around it that places it on the page. */
// A type, not an interface, so it is the JSON the agent's tools return.
type Selected = {
  text: string
  before: string
  after: string
  heading: string | null
}

export function PageView({
  repository,
  path,
}: {
  repository: string
  path: string
}) {
  const host = usePluginHost()
  const { user } = useHostContext()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const github = useMemo(() => host.github(repository), [host, repository])
  const space = useSpace(repository).data!
  const drafts = useDrafts(repository)
  const draft = drafts.data?.get(path) ?? null
  const ref = draft?.branch ?? space.defaultBranch
  const head = useHead(repository, ref)
  const file = useFile(repository, path, draft?.head ?? head.data ?? undefined)
  const editing = draft !== null
  const { live, synced } = useLiveDoc(
    repository,
    draft ? draftRoom(draft, path) : null
  )
  const viewers = usePresence(repository, path, editing)
  const comments = useComments(repository, path)
  const putComment = usePutComment(repository, path)
  const pull = usePull(repository, draft?.branch)

  const editor = useRef<Editor | null>(null)
  // Starting a draft swaps the read-only editor for the live one; only ever
  // touch the one still mounted.
  const mounted = (): Editor | null =>
    editor.current && !editor.current.isDestroyed ? editor.current : null
  // Only the live editor holds a draft's edits; the read-only one shows the
  // page as committed, perhaps at an older commit while a newer one loads.
  const liveMounted = (): Editor | null => {
    const ready = mounted()
    return ready?.isEditable ? ready : null
  }
  const [doc, setDoc] = useState<ProseMirrorNode | null>(null)
  const [current, setCurrent] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>("comments")
  const [composing, setComposing] = useState<Composing | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [discarding, setDiscarding] = useState(false)
  const [replacingEdits, setReplacingEdits] = useState(false)
  // Below `lg` the side panel is a sheet over the page, opened on demand.
  const narrow = useNarrowWindow()
  const [sheetOpen, setSheetOpen] = useState(false)
  const showSide = (next: Tab) => {
    setTab(next)
    setSheetOpen(true)
  }
  const closeSheet = () => {
    setSheetOpen(false)
    setComposing(null)
  }

  // The live editor seeds a new draft's shared document from the page on
  // screen, so it first opens only once the file at the draft's head has
  // loaded, never while the commit before it stays up; once open, it stays
  // open while someone's newer save loads.
  const [liveOpened, setLiveOpened] = useState<LiveDoc | null>(null)
  const liveShown =
    editing && synced && (live === liveOpened || !file.isPlaceholderData)
      ? live
      : null
  if (liveShown !== null && liveShown !== liveOpened) setLiveOpened(liveShown)

  // Opening the draft swaps the read-only editor for the live one, and the page
  // briefly loses its height; put the reader back where they were.
  const scroller = useRef<HTMLDivElement>(null)
  const scrolled = useRef(0)
  useLayoutEffect(() => {
    const element = scroller.current
    if (!element) return
    // Kept before the swap: the collapse's own scroll event would overwrite it.
    const top = scrolled.current
    element.scrollTop = top
    const frame = requestAnimationFrame(() => {
      element.scrollTop = top
      scrolled.current = top
    })
    return () => cancelAnimationFrame(frame)
  }, [liveShown])

  const split = useMemo(() => splitFrontMatter(file.data ?? ""), [file.data])
  // What the saved Markdown reads as once the editor has normalised it, so a
  // page the serialiser would merely reformat does not count as changed.
  const [saved, setSaved] = useState<string | null>(null)
  useEffect(() => {
    const ready = mounted()
    if (ready) setSaved(asLiveMarkdown(ready, split.body))
  }, [split.body])
  // Trailing blank lines are the editor's own (an empty paragraph after a
  // table), never the page's.
  const dirty =
    liveShown !== null &&
    current !== null &&
    saved !== null &&
    current.trimEnd() !== saved.trimEnd()
  // Commits on the draft's branch that Docs didn't save. Only those that
  // changed this page need loading; mid-refetch, a save someone made in Docs
  // can look like one for a moment.
  const headMoved =
    editing &&
    !drafts.isFetching &&
    !head.isFetching &&
    Boolean(head.data) &&
    draft.head !== head.data
  const atHead = useQuery({
    ...fileQuery(host, repository, path, head.data ?? ""),
    enabled: headMoved,
  })
  const outsideCommits =
    headMoved &&
    !file.isPlaceholderData &&
    atHead.data !== undefined &&
    atHead.data !== file.data

  const people = usePeople(
    useMemo(
      () => [
        ...viewers.map((viewer) => viewer.login),
        ...(comments.data ?? []).map((stored) => stored.document.author),
      ],
      [viewers, comments.data]
    )
  )

  const all = comments.data ?? []
  const threads: CommentThread[] = all
    .filter((stored) => stored.document.parent === null)
    .map((stored) => ({
      id: stored.key,
      comment: stored.document,
      replies: all
        .filter((reply) => reply.document.parent === stored.key)
        .map((reply) => ({ id: reply.key, comment: reply.document }))
        .sort((a, b) => a.comment.createdAt.localeCompare(b.comment.createdAt)),
      anchored: doc ? resolveAnchor(doc, stored.document) !== null : true,
    }))
    .sort((a, b) => a.comment.createdAt.localeCompare(b.comment.createdAt))

  // From the comments alone, not `threads`, which change with every keystroke,
  // so the editor redraws highlights only when the comments change.
  const highlights = useMemo(
    (): Highlight[] =>
      (comments.data ?? [])
        .filter(
          ({ document }) =>
            document.parent === null && document.status === "open"
        )
        .map(({ key, document }) => ({
          id: key,
          anchor: document,
          kind: document.suggestion === null ? "comment" : "suggestion",
        })),
    [comments.data]
  )

  const act = (label: string) => (work: () => Promise<void>) => {
    setBusy(label)
    work()
      .catch((error: unknown) =>
        host.ui.toast({
          title: `Couldn't ${label.toLowerCase()}`,
          description: errorMessage(error),
          intent: "error",
        })
      )
      .finally(() => setBusy(null))
  }

  const refreshDraft = async () => {
    await queryClient.invalidateQueries({ queryKey: keys.drafts(repository) })
    await queryClient.invalidateQueries({
      queryKey: keys.head(repository, ref),
    })
  }

  const putDraft = (next: Draft) => draftsIn(host, repository).put(path, next)

  const startingDraft = useRef<Promise<Draft> | null>(null)
  const startDraft = async (): Promise<Draft> => {
    const base = await github.ref(space.defaultBranch)
    if (!base) throw new Error(`${space.defaultBranch} no longer exists`)
    const mine: Draft = {
      branch: draftBranch(path),
      base: space.defaultBranch,
      head: base,
      startedBy: user.login,
      startedAt: new Date().toISOString(),
    }
    await github.createBranch(mine.branch, base)
    // The branch starts at the commit on screen, so the page needs nothing new
    // to stay up while the draft takes over.
    queryClient.setQueryData(keys.head(repository, mine.branch), base)
    if (
      head.data === base &&
      file.data !== undefined &&
      !file.isPlaceholderData
    )
      queryClient.setQueryData(keys.file(repository, path, base), file.data)
    // A page has one draft: when two people start one at once, the first
    // stored wins and everyone joins it before the live editor opens.
    const started = (await draftsIn(host, repository).create(path, mine))
      .document
    if (started.branch !== mine.branch)
      void github.deleteBranch(mine.branch).catch(
        (error: unknown) =>
          void host.ui.toast({
            title: `Couldn't delete the unused branch ${mine.branch}`,
            description: errorMessage(error),
            intent: "error",
          })
      )
    queryClient.setQueryData<Map<string, Draft>>(
      keys.drafts(repository),
      (open) => new Map(open).set(path, started)
    )
    await refreshDraft()
    return started
  }

  const ensureDraft = async (): Promise<Draft> => {
    const active =
      draft ??
      queryClient
        .getQueryData<Map<string, Draft>>(keys.drafts(repository))
        ?.get(path)
    if (active) return active
    startingDraft.current ??= startDraft()
    try {
      return await startingDraft.current
    } finally {
      startingDraft.current = null
    }
  }

  const pageAt = (sha: string) =>
    queryClient.fetchQuery(fileQuery(host, repository, path, sha))

  const save = async (message?: string) => {
    const ready = liveMounted()
    if (!draft || !head.data || !ready) return
    // Commits made outside Docs that left this page alone are saved on top of.
    const [base, atBranchHead] = await Promise.all([
      pageAt(draft.head),
      pageAt(head.data),
    ])
    if (atBranchHead !== base)
      throw new Error(
        "Load the commits made outside Docs before saving this draft."
      )
    const { frontMatter, body } = splitFrontMatter(base ?? "")
    const text =
      frontMatter +
      withTrailingNewline(
        preserveFormatting(body, ready.getMarkdown(), (markdown) =>
          asLiveMarkdown(ready, markdown)
        )
      )
    const sha = await github.commit({
      branch: draft.branch,
      expectedHead: head.data,
      message: message?.trim() || `Update ${path}`,
      files: { [path]: text },
    })
    await putDraft({ ...draft, head: sha })
    queryClient.setQueryData(keys.head(repository, ref), sha)
    queryClient.setQueryData(keys.file(repository, path, sha), text)
    await refreshDraft()
  }

  const loadOutsideCommits = async () => {
    const ready = liveMounted()
    if (!draft || !head.data || !ready) return
    const text = await github.file(path, head.data)
    ready.commands.setContent(splitFrontMatter(text ?? "").body, {
      contentType: "markdown",
    })
    await putDraft({ ...draft, head: head.data })
    setReplacingEdits(false)
    await refreshDraft()
  }

  // A new page isn't on its branch until someone writes it, and GitHub opens
  // no pull request from a branch with nothing in it.
  const unwritten =
    editing && file.data === null && !file.isPlaceholderData && !dirty

  const publish = async () => {
    const active = await ensureDraft()
    if (dirty) await save()
    const existing = await github.findPull(active.branch)
    const opened =
      existing ??
      (await github.openPull({
        head: active.branch,
        base: active.base,
        title: `${file.data === null ? "Add" : "Update"} ${pageTitle(path)}`,
        body: `Published from Docs.\n\nPage: \`${path}\``,
      }))
    await queryClient.invalidateQueries({
      queryKey: keys.pull(repository, active.branch),
    })
    void host.ui.toast({
      title: existing
        ? `Pull request #${opened.number} is up to date`
        : `Opened pull request #${opened.number}`,
      intent: "success",
    })
  }

  const closeDraft = async () => {
    await draftsIn(host, repository).delete(path)
    await refreshDraft()
  }

  /** Throws the draft away: its branch with every change, saved or not, and any pull request from it. */
  const discardDraft = async () => {
    if (!draft) return
    await github.deleteBranch(draft.branch).catch((error: unknown) => {
      // Already gone, as when its pull request merged and GitHub deleted it.
      if (
        !(
          error instanceof HostError &&
          (error.status === 404 || error.status === 422)
        )
      )
        throw error
    })
    setDiscarding(false)
    await closeDraft()
  }

  const newComment = (
    fields: Pick<
      PageComment,
      "parent" | "quote" | "prefix" | "suffix" | "body" | "suggestion"
    >
  ): PageComment => ({
    path,
    ...fields,
    author: user.login,
    createdAt: new Date().toISOString(),
    status: "open",
  })

  const setStatus = (thread: CommentThread, status: CommentStatus) =>
    putComment.mutate({
      id: thread.id,
      comment: { ...thread.comment, status },
      failure: STATUS_FAILURE[status],
    })

  const acceptSuggestion = async (thread: CommentThread) => {
    // Accepting may start the draft, so the live editor may not be up yet.
    const { view } = await liveEditor()
    const range = resolveAnchor(view.state.doc, thread.comment)
    if (!range || thread.comment.suggestion === null)
      throw new Error("The passage it replaces is no longer in the page.")
    const command = view.chain().focus()
    const applied = thread.comment.suggestion
      ? command
          .insertContentAt(range, {
            type: "text",
            text: thread.comment.suggestion,
          })
          .run()
      : command.deleteRange(range).run()
    if (!applied) throw new Error("The suggestion couldn't be applied.")
    setStatus(thread, "accepted")
  }

  const openComments = threads.filter((t) => t.comment.status === "open")
  const askInChat = (draftText?: string) =>
    void host.agent.open(draftText ? { draft: draftText } : {})

  /** The live editor, starting the draft first; tools that change the page wait for it. */
  const liveEditor = async (): Promise<{ view: Editor; active: Draft }> => {
    const active = await ensureDraft()
    for (let tries = 0; tries < 150; tries++) {
      const ready = liveMounted()
      if (ready) return { view: ready, active }
      await new Promise((resolve) => setTimeout(resolve, 100))
    }
    throw new Error("The draft didn't open in time; try again.")
  }

  /** What the person selected on the page, with the text around it that places it. */
  const selectedPassage = (): Selected | null => {
    const view = mounted()?.view
    const selection = document.getSelection()
    if (!view || !selection || selection.isCollapsed) return null
    const range = selection.getRangeAt(0)
    if (!view.dom.contains(range.commonAncestorContainer)) return null
    try {
      const from = view.posAtDOM(range.startContainer, range.startOffset)
      const to = view.posAtDOM(range.endContainer, range.endOffset)
      const anchor = anchorFor(
        view.state.doc,
        Math.min(from, to),
        Math.max(from, to),
        240
      )
      if (!anchor?.quote.trim()) return null
      return {
        text: anchor.quote.slice(0, 2_000),
        before: anchor.prefix,
        after: anchor.suffix,
        heading: headingBefore(view.state.doc, Math.min(from, to)),
      }
    } catch {
      return null
    }
  }

  // What the person selected, kept current so each message to the agent carries it.
  const [selection, setSelection] = useState<Selected | null>(null)
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    const changed = () => {
      clearTimeout(timer)
      timer = setTimeout(() => setSelection(selectedPassage()), 250)
    }
    document.addEventListener("selectionchange", changed)
    return () => {
      clearTimeout(timer)
      document.removeEventListener("selectionchange", changed)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reads the editor mounted when it fires.
  }, [])
  const words = selection ? selection.text.trim().split(/\s+/).length : 0
  useSharedContext(
    {
      plugin: "Docs, an editor for the Markdown pages of a GitHub repository",
      repository,
      page: path,
      title: pageTitle(path),
      draft: draft
        ? { branch: draft.branch, startedBy: draft.startedBy }
        : null,
      selection: selection && {
        ...selection,
        note: "`text` is what the person selected. `before` and `after` are the text right around it, only to show where it is; they are not selected.",
      },
    },
    `${pageTitle(path)}${words ? ` · ${words} word${words === 1 ? "" : "s"} selected` : ""}`
  )

  useOpenPageTools({
    path,
    ready: () =>
      mounted() !== null &&
      !file.isPlaceholderData &&
      (!editing || liveMounted() !== null),
    read: () => ({
      repository,
      path,
      draft: draft
        ? { branch: draft.branch, startedBy: draft.startedBy }
        : null,
      markdown: mounted()?.getMarkdown() ?? split.body,
      selection: selectedPassage(),
      openComments: openComments.map(({ id, comment }) => ({
        id,
        quote: comment.quote,
        body: comment.body,
        suggestion: comment.suggestion,
        author: comment.author,
      })),
    }),
    edit: async (find, replace) => {
      const { view, active } = await liveEditor()
      const range = resolveAnchor(view.state.doc, {
        quote: find,
        prefix: "",
        suffix: "",
      })
      if (!range)
        throw new Error("That text isn't on the page; read_page shows what is.")
      const parsed = view.markdown?.parse(replace)
      const blocks = parsed?.content ?? []
      // One paragraph replaces text within its block rather than splitting it.
      const content =
        blocks.length === 1 && blocks[0]?.type === "paragraph"
          ? (blocks[0].content ?? [])
          : blocks
      view.chain().insertContentAt(range, content).run()
      return { edited: path, draft: active.branch }
    },
    comment: async (quote, body, suggestion) => {
      const view = mounted()
      const range =
        view && resolveAnchor(view.state.doc, { quote, prefix: "", suffix: "" })
      const anchor =
        view && range && anchorFor(view.state.doc, range.from, range.to)
      if (!anchor)
        throw new Error("That text isn't on the page; read_page shows what is.")
      const id = crypto.randomUUID()
      await putComment.mutateAsync({
        id,
        comment: newComment({ parent: null, ...anchor, body, suggestion }),
        failure: "Couldn't add the agent's comment",
      })
      setTab("comments")
      setActiveId(id)
      return { commented: id }
    },
  })

  // Images in the repository, loaded once each as the person's GitHub access allows.
  const images = useRef(new Map<string, Promise<string | null>>())
  useEffect(() => {
    const loaded = images.current
    return () => {
      for (const url of loaded.values())
        void url.then((u) => u && URL.revokeObjectURL(u))
      loaded.clear()
    }
  }, [ref])
  const loadImage = (image: ImageFile) => {
    const key = JSON.stringify(image)
    let url = images.current.get(key)
    if (!url) {
      // Another repository's file loads only if the person shared it too.
      url = host
        .github(image.repository ?? repository)
        .image(image.path, image.ref ?? ref)
        .then((blob) => (blob ? URL.createObjectURL(blob) : null))
        .catch(() => null)
      images.current.set(key, url)
    }
    return url
  }

  const onLink = (href: string) => {
    const target = resolveLink(href, path)
    if (!target) return
    if ("url" in target) return void host.nav.openExternal(target.url)
    const [owner = "", repo = ""] = repository.split("/")
    void navigate({
      to: "/$owner/$repo/$",
      params: { owner, repo, _splat: target.page },
    })
  }

  if (editing && head.data === null)
    return (
      <div className="flex flex-col items-start gap-space-3 p-space-6">
        <p className="text-sm text-secondary">
          This draft's branch, <code>{draft.branch}</code>, no longer exists,
          perhaps because its pull request was merged. Its unsaved edits went
          with it.
        </p>
        <Button size="sm" variant="outlined" onClick={() => void closeDraft()}>
          Discard draft
        </Button>
      </div>
    )
  if (head.data === null)
    return (
      <p className="text-error p-space-6 text-sm">
        Branch {ref} no longer exists
      </p>
    )
  if (head.error || file.error)
    return (
      <p className="text-error p-space-6 text-sm">
        {(head.error ?? file.error)?.message}
      </p>
    )
  if (file.data === undefined)
    return (
      <div className="flex flex-1 items-center justify-center">
        <Spinner />
      </div>
    )

  const missing = file.data === null
  const me = { name: user.name || user.login, color: colorFor(user.login) }

  const side = (
    <>
      <nav className="flex gap-space-1 border-b border-default p-space-2">
        {(["comments", "activity"] as const).map((name) => (
          <button
            key={name}
            type="button"
            onClick={() => setTab(name)}
            className={`h-7 rounded-md px-space-2 text-sm capitalize ${
              tab === name
                ? "bg-surface-level-2-hover text-primary"
                : "text-secondary hover:text-primary"
            }`}
          >
            {name}
            {name === "comments" && openComments.length > 0
              ? ` ${openComments.length}`
              : ""}
          </button>
        ))}
        {narrow && (
          <IconButton
            icon={XIcon}
            label="Close"
            size="sm"
            variant="plain"
            className="ml-auto"
            onClick={closeSheet}
          />
        )}
      </nav>
      {tab === "comments" ? (
        <CommentsPanel
          threads={threads}
          people={people}
          composing={composing}
          activeId={activeId}
          onCompose={(body, suggestion) => {
            if (!composing) return
            putComment.mutate({
              id: crypto.randomUUID(),
              comment: newComment({
                parent: null,
                ...composing.anchor,
                body,
                suggestion,
              }),
              failure:
                suggestion === null
                  ? "Couldn't post the comment"
                  : "Couldn't post the suggestion",
            })
            setComposing(null)
          }}
          onCancelCompose={() => setComposing(null)}
          onSelect={setActiveId}
          onReply={(parent, body) =>
            putComment.mutate({
              id: crypto.randomUUID(),
              comment: newComment({
                parent,
                quote: "",
                prefix: "",
                suffix: "",
                body,
                suggestion: null,
              }),
              failure: "Couldn't post the reply",
            })
          }
          onSetStatus={setStatus}
          onAccept={(thread) =>
            act("Accept suggestion")(() => acceptSuggestion(thread))
          }
        />
      ) : (
        <Activity repository={repository} path={path} gitRef={ref} />
      )}
    </>
  )

  return (
    <div className="flex min-h-0 flex-1">
      <div
        ref={scroller}
        onScroll={(event) => {
          scrolled.current = event.currentTarget.scrollTop
        }}
        className="min-w-0 flex-1 overflow-y-auto"
      >
        <header className="bg-surface-level-0/95 sticky top-0 z-10 flex h-12 items-center gap-space-2 border-b border-default px-space-5 backdrop-blur">
          <span className="truncate text-sm text-secondary">
            {path.split("/").slice(0, -1).join(" / ") || repository}
          </span>
          <Badge size="xs" color={editing ? "warning" : "secondary"}>
            {editing ? "Draft" : "Published"}
          </Badge>
          {dirty && (
            <span className="text-xs text-tertiary">Unsaved changes</span>
          )}
          {pull.data && (
            <button
              type="button"
              className="text-xs text-secondary underline-offset-2 hover:text-primary hover:underline"
              onClick={() => void host.nav.openExternal(pull.data!.url)}
            >
              PR #{pull.data.number}
            </button>
          )}
          <span className="flex-1" />
          <Viewers viewers={viewers} people={people} />
          {editing ? (
            <>
              <Button
                size="sm"
                variant="plain"
                disabled={busy !== null}
                onClick={() => setDiscarding(true)}
              >
                Discard
              </Button>
              <SaveButton
                disabled={!dirty || busy !== null}
                onSave={(message) => act("Save version")(() => save(message))}
              />
              <Button
                size="sm"
                disabled={busy !== null || unwritten}
                title={unwritten ? "Write the page to publish it" : undefined}
                loading={busy === "Publish"}
                onClick={() => act("Publish")(publish)}
              >
                {pull.data ? "Update PR" : "Publish"}
              </Button>
            </>
          ) : (
            <Button
              size="sm"
              loading={busy === "Start editing"}
              onClick={() =>
                act("Start editing")(async () => void (await ensureDraft()))
              }
            >
              Edit
            </Button>
          )}
          {narrow && (
            <IconButton
              icon={ChatCircleTextIcon}
              label={
                openComments.length > 0
                  ? `Comments (${openComments.length} open)`
                  : "Comments"
              }
              size="sm"
              variant="plain"
              onClick={() => showSide("comments")}
            />
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <IconButton
                icon={DotsThreeIcon}
                label="More"
                size="sm"
                variant="plain"
              />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-64">
              <DropdownMenuItem onSelect={() => askInChat()}>
                <SparkleIcon size={14} /> Ask the agent
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={openComments.length === 0}
                onSelect={() =>
                  askInChat("Address the open comments on this page.")
                }
              >
                <SparkleIcon size={14} /> Ask the agent to address{" "}
                {openComments.length} open comment
                {openComments.length === 1 ? "" : "s"}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onSelect={() =>
                  void host.nav.openExternal(
                    `https://github.com/${repository}/blob/${ref}/${path}`
                  )
                }
              >
                View on GitHub
              </DropdownMenuItem>
              {editing && (
                <DropdownMenuItem
                  disabled={dirty}
                  onSelect={() => act("Close draft")(closeDraft)}
                >
                  Close draft (keeps the branch)
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </header>
        <article className="pb-space-12 mx-auto w-full max-w-3xl px-space-6 pt-space-8">
          {outsideCommits && (
            <Banner intent="neutral" className="mb-space-4">
              <span className="flex flex-wrap items-center gap-space-3">
                This draft has commits made outside Docs. Load them before
                saving.
                <Button
                  size="xs"
                  variant="outlined"
                  disabled={busy !== null}
                  onClick={() =>
                    dirty
                      ? setReplacingEdits(true)
                      : act("Load commits")(loadOutsideCommits)
                  }
                >
                  Load them
                </Button>
              </span>
            </Banner>
          )}
          {outsideCommits && replacingEdits && (
            <Banner intent="warning" className="mb-space-4">
              <span className="flex flex-wrap items-center gap-space-3">
                Loading these commits replaces the unsaved edits in this shared
                draft. Copy any edits you want to keep first.
                <Button
                  size="xs"
                  color="error"
                  disabled={busy !== null}
                  loading={busy === "Load commits"}
                  onClick={() => act("Load commits")(loadOutsideCommits)}
                >
                  Replace unsaved edits
                </Button>
                <Button
                  size="xs"
                  variant="outlined"
                  onClick={() => setReplacingEdits(false)}
                >
                  Keep editing
                </Button>
              </span>
            </Banner>
          )}
          {editing && discarding && (
            <Banner intent="warning" className="mb-space-4">
              <span
                className="flex flex-wrap items-center gap-space-3"
                onKeyDown={(event) => {
                  if (event.key === "Escape") setDiscarding(false)
                }}
              >
                Discard this draft? Every change in it, saved or not, goes with
                its branch
                {pull.data
                  ? `, and pull request #${pull.data.number} closes`
                  : ""}
                .
                <span className="flex gap-space-2">
                  <Button
                    size="xs"
                    color="error"
                    loading={busy === "Discard draft"}
                    onClick={() => act("Discard draft")(discardDraft)}
                    autoFocus
                  >
                    Discard draft
                  </Button>
                  <Button
                    size="xs"
                    variant="outlined"
                    onClick={() => setDiscarding(false)}
                  >
                    Keep editing
                  </Button>
                </span>
              </span>
            </Banner>
          )}
          {editing && draft.startedBy !== user.login && (
            <p className="mb-space-3 text-xs text-tertiary">
              {draft.startedBy} started this draft {timeAgo(draft.startedAt)}.
            </p>
          )}
          {!/^\s*#\s/.test(split.body) && (
            <h1 className="mb-space-4 text-4xl font-semibold text-primary capitalize">
              {pageTitle(path)}
            </h1>
          )}
          {missing && !editing && (
            <p className="text-sm text-secondary">
              This page isn&apos;t on {space.defaultBranch} yet. Choose Edit to
              write it.
            </p>
          )}
          <PageEditor
            markdown={split.body}
            // Until the draft's live document syncs and the page as the draft
            // has it loads, the page stays as it was.
            live={liveShown}
            me={me}
            highlights={highlights}
            activeId={activeId}
            actions={{
              comment: (anchor) => {
                setComposing({ anchor, kind: "comment" })
                showSide("comments")
              },
              suggest: (anchor) => {
                setComposing({ anchor, kind: "suggestion" })
                showSide("comments")
              },
              // The selection reaches the agent as page context; page text
              // never goes in the person's own message.
              askAgent: () => askInChat("About the passage I selected: "),
            }}
            onSelectComment={(id) => {
              setActiveId(id)
              showSide("comments")
            }}
            onLink={onLink}
            page={path}
            loadImage={loadImage}
            onReady={(ready) => {
              editor.current = ready
              setSaved(asLiveMarkdown(ready, split.body))
              setCurrent(ready.getMarkdown())
              setDoc(ready.state.doc)
            }}
            onChange={(changed) => {
              setCurrent(changed.getMarkdown())
              setDoc(changed.state.doc)
            }}
          />
        </article>
      </div>
      {narrow ? (
        <SideSheet
          title="Comments and activity"
          open={sheetOpen}
          onClose={closeSheet}
        >
          {side}
        </SideSheet>
      ) : (
        <aside className="flex w-80 shrink-0 flex-col border-l border-default">
          {side}
        </aside>
      )}
    </div>
  )
}

function Viewers({
  viewers,
  people,
}: {
  viewers: Array<{ login: string; color: string; editing: boolean }>
  people: Map<string, { name: string; picture: string | null }>
}) {
  if (viewers.length === 0) return null
  const shown = viewers.slice(0, 5)
  return (
    <div
      className="flex items-center -space-x-1.5"
      aria-label={`${viewers.length} viewing`}
    >
      {shown.map((viewer) => {
        const person = people.get(viewer.login.toLowerCase())
        const name = person?.name ?? viewer.login
        return (
          <span
            key={viewer.login}
            className="flex rounded-full ring-2 ring-[var(--bg-primary)]"
          >
            <Avatar
              size="md"
              shape="circle"
              label={name}
              imageUrl={person?.picture ?? undefined}
              color={viewer.color}
              tooltip={`${name}${viewer.editing ? " · editing" : " · viewing"}`}
            />
          </span>
        )
      })}
      {viewers.length > shown.length && (
        <span className="pl-space-2 text-xs text-secondary">
          +{viewers.length - shown.length}
        </span>
      )}
    </div>
  )
}

/** Save version, opening in place into a short note on what changed. */
function SaveButton({
  disabled,
  onSave,
}: {
  disabled: boolean
  onSave: (message: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [message, setMessage] = useState("")
  if (!open)
    return (
      <Button
        size="sm"
        variant="outlined"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        Save version
      </Button>
    )
  return (
    <form
      className="flex items-center gap-space-1"
      onSubmit={(event) => {
        event.preventDefault()
        onSave(message)
        setMessage("")
        setOpen(false)
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") setOpen(false)
      }}
    >
      <input
        aria-label="What changed"
        placeholder="What changed? (optional)"
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        autoFocus
        className="h-6 w-56 rounded-md border border-default bg-transparent px-space-2 text-xs text-primary outline-none placeholder:text-placeholder focus:border-[var(--border-focus)]"
      />
      <Button type="submit" size="sm" disabled={disabled}>
        Save
      </Button>
      <Button
        type="button"
        size="sm"
        variant="plain"
        onClick={() => setOpen(false)}
      >
        Cancel
      </Button>
    </form>
  )
}

function Activity({
  repository,
  path,
  gitRef,
}: {
  repository: string
  path: string
  gitRef: string
}) {
  const host = usePluginHost()
  const history = useHistory(repository, path, gitRef)
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-space-4 overflow-y-auto p-space-3">
      <section className="flex flex-col gap-space-2">
        <h3 className="text-xs font-medium text-secondary">
          Versions on {gitRef}
        </h3>
        {history.isPending && <Spinner />}
        {history.data?.length === 0 && (
          <p className="text-sm text-secondary">None yet.</p>
        )}
        <ol className="flex flex-col gap-space-2">
          {history.data?.map((commit) => (
            <li key={commit.sha}>
              <button
                type="button"
                className="text-left text-sm text-primary hover:underline"
                onClick={() =>
                  void host.nav.openExternal(
                    `https://github.com/${repository}/commit/${commit.sha}`
                  )
                }
              >
                {commit.message.split("\n")[0]}
              </button>
              <p className="text-xs text-tertiary">
                {commit.authorLogin ?? commit.authorName}
                {commit.authoredAt ? ` · ${timeAgo(commit.authoredAt)}` : ""}
              </p>
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}

/** Below Tailwind's `lg`, where the side panel no longer fits beside the page. */
const NARROW = "(width < 64rem)"

function watchNarrow(changed: () => void): () => void {
  const query = window.matchMedia(NARROW)
  query.addEventListener("change", changed)
  return () => query.removeEventListener("change", changed)
}

function useNarrowWindow(): boolean {
  return useSyncExternalStore(
    watchNarrow,
    () => window.matchMedia(NARROW).matches,
    () => false
  )
}
