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
import { Textarea } from "@langchain/macaw-components/Textarea"
import { useHostContext, usePluginHost } from "@openswe/plugin-sdk/react"
import { DotsThreeIcon } from "@phosphor-icons/react/dist/ssr/DotsThree"
import { SparkleIcon } from "@phosphor-icons/react/dist/ssr/Sparkle"
import { useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import type { Editor } from "@tiptap/react"
import type { Node as ProseMirrorNode } from "@tiptap/pm/model"
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"

import {
  CommentsPanel,
  type CommentThread,
  type Composing,
} from "@/components/Comments"
import { PageEditor } from "@/components/Editor"
import { resolveAnchor, type Highlight } from "@/lib/anchors"
import { errorMessage } from "@/lib/host"
import { asLiveMarkdown, useLiveDoc, usePresence } from "@/lib/live"
import { preserveFormatting } from "@/lib/preserve"
import {
  commentsIn,
  draftsIn,
  colorFor,
  draftBranch,
  draftRoom,
  pageTitle,
  resolveLink,
  splitFrontMatter,
  timeAgo,
  withTrailingNewline,
  type Anchor,
  type CommentStatus,
  type Draft,
  type PageComment,
} from "@/lib/model"
import {
  keys,
  useComments,
  useDrafts,
  useFile,
  useHead,
  useHistory,
  usePeople,
  usePull,
  useSpace,
  useThreads,
} from "@/lib/queries"

type Tab = "comments" | "activity"

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
  const draft = useDrafts(repository).data?.get(path) ?? null
  const ref = draft?.branch ?? space.defaultBranch
  const head = useHead(repository, ref)
  const file = useFile(repository, path, head.data ?? undefined)
  const editing = draft !== null
  const { live, synced } = useLiveDoc(
    repository,
    draft ? draftRoom(draft, path) : null
  )
  const viewers = usePresence(repository, path, editing)
  const comments = useComments(repository, path)
  const pull = usePull(repository, draft?.branch)

  const editor = useRef<Editor | null>(null)
  // Starting a draft swaps the read-only editor for the live one; only ever
  // touch the one still mounted.
  const mounted = (): Editor | null =>
    editor.current && !editor.current.isDestroyed ? editor.current : null
  const [doc, setDoc] = useState<ProseMirrorNode | null>(null)
  const [current, setCurrent] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>("comments")
  const [composing, setComposing] = useState<Composing | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [asking, setAsking] = useState<{ anchor: Anchor | null } | null>(null)
  const [pendingAccept, setPendingAccept] = useState<string | null>(null)

  // Opening the draft swaps the read-only editor for the live one, and the page
  // briefly loses its height; put the reader back where they were.
  const scroller = useRef<HTMLDivElement>(null)
  const scrolled = useRef(0)
  const liveShown = editing && synced ? live : null
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
  const normalize = (ready: Editor, markdown: string) =>
    asLiveMarkdown(ready, markdown)
  useEffect(() => {
    const ready = mounted()
    if (ready) setSaved(normalize(ready, split.body))
  }, [split.body])
  // Trailing blank lines are the editor's own (an empty paragraph after a
  // table), never the page's.
  const dirty =
    editing &&
    current !== null &&
    saved !== null &&
    current.trimEnd() !== saved.trimEnd()
  const outsideCommits =
    editing && Boolean(head.data) && draft.head !== head.data && !dirty

  const people = usePeople(
    useMemo(
      () => [
        ...viewers.map((viewer) => viewer.login),
        ...(comments.data ?? []).map((stored) => stored.document.author),
      ],
      [viewers, comments.data]
    )
  )

  const threads = useMemo((): CommentThread[] => {
    const all = comments.data ?? []
    return all
      .filter((stored) => stored.document.parent === null)
      .map((stored) => ({
        id: stored.key,
        comment: stored.document,
        replies: all
          .filter((reply) => reply.document.parent === stored.key)
          .map((reply) => ({ id: reply.key, comment: reply.document }))
          .sort((a, b) =>
            a.comment.createdAt.localeCompare(b.comment.createdAt)
          ),
        anchored: doc ? resolveAnchor(doc, stored.document) !== null : true,
      }))
      .sort((a, b) => a.comment.createdAt.localeCompare(b.comment.createdAt))
  }, [comments.data, doc])

  const highlights = useMemo(
    (): Highlight[] =>
      threads
        .filter((thread) => thread.comment.status === "open")
        .map((thread) => ({
          id: thread.id,
          anchor: thread.comment,
          kind: thread.comment.suggestion === null ? "comment" : "suggestion",
        })),
    [threads]
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

  const ensureDraft = async (): Promise<Draft> => {
    if (draft) return draft
    const base = await github.ref(space.defaultBranch)
    if (!base) throw new Error(`${space.defaultBranch} no longer exists`)
    const started: Draft = {
      branch: draftBranch(path),
      base: space.defaultBranch,
      head: base,
      startedBy: user.login,
      startedAt: new Date().toISOString(),
    }
    await github.createBranch(started.branch, base)
    // The branch starts at the commit on screen, so the page needs nothing new
    // to stay up while the draft takes over.
    queryClient.setQueryData(keys.head(repository, started.branch), base)
    if (head.data === base && file.data !== undefined)
      queryClient.setQueryData(keys.file(repository, path, base), file.data)
    await putDraft(started)
    queryClient.setQueryData<Map<string, Draft>>(
      keys.drafts(repository),
      (drafts) => new Map(drafts).set(path, started)
    )
    await refreshDraft()
    return started
  }

  const save = async (message?: string) => {
    const ready = mounted()
    if (!draft || !head.data || !ready) return
    const body = preserveFormatting(
      split.body.trimEnd(),
      (saved ?? normalize(ready, split.body)).trimEnd(),
      ready.getMarkdown().trimEnd()
    )
    const text = split.frontMatter + withTrailingNewline(body)
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
    const ready = mounted()
    if (!draft || !head.data || !ready) return
    const text = await github.file(path, head.data)
    ready.commands.setContent(splitFrontMatter(text ?? "").body, {
      contentType: "markdown",
    })
    await putDraft({ ...draft, head: head.data })
    await refreshDraft()
  }

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

  const putComment = async (id: string, comment: PageComment) => {
    await commentsIn(host, repository).put(id, comment)
    await queryClient.invalidateQueries({
      queryKey: keys.comments(repository, path),
    })
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

  const setStatus = async (id: string, status: CommentStatus) => {
    const thread = threads.find((t) => t.id === id)
    if (thread) await putComment(id, { ...thread.comment, status })
  }

  const applySuggestion = async (thread: CommentThread) => {
    const view = mounted()
    const range = view && resolveAnchor(view.state.doc, thread.comment)
    if (!view || !range || thread.comment.suggestion === null) return
    view
      .chain()
      .focus()
      .insertContentAt(range, { type: "text", text: thread.comment.suggestion })
      .run()
    await setStatus(thread.id, "accepted")
  }

  // An accept that had to start the draft first applies once the live editor is up.
  useEffect(() => {
    if (!pendingAccept || !live || !synced || !doc) return
    const thread = threads.find((t) => t.id === pendingAccept)
    setPendingAccept(null)
    if (thread) act("Accept suggestion")(() => applySuggestion(thread))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs when the editor becomes live.
  }, [pendingAccept, live, synced, doc])

  const askAgent = async (instruction: string, anchor: Anchor | null) => {
    const active = await ensureDraft()
    if (dirty) await save()
    const request = [
      `Edit \`${path}\` in ${repository} on the existing branch \`${active.branch}\`.`,
      "",
      instruction,
      ...(anchor
        ? [
            "",
            "This applies to the passage:",
            "",
            `> ${anchor.quote.replace(/\n/g, "\n> ")}`,
          ]
        : []),
      "",
      `Commit your change to \`${active.branch}\` and push it. Do not open a pull request, and leave the rest of the page as it is.`,
    ].join("\n")
    const started = await host.threads.start({
      repository,
      title: `Docs: ${pageTitle(path)}`,
      request,
    })
    if (started) {
      await queryClient.invalidateQueries({ queryKey: keys.threads })
      void host.ui.toast({
        title: "The agent is on it",
        description:
          "Its commit lands on this draft; you'll be offered it here.",
        intent: "success",
      })
    }
  }

  const openComments = threads.filter((t) => t.comment.status === "open")
  const addressComments = () =>
    askAgent(
      [
        "Address these review comments on the page:",
        "",
        ...openComments.map(({ comment }) =>
          comment.suggestion !== null
            ? `- Replace "${comment.quote}" with "${comment.suggestion}"${comment.body ? ` (${comment.body})` : ""}`
            : `- On "${comment.quote}": ${comment.body}`
        ),
      ].join("\n"),
      null
    )

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
              <SaveButton
                disabled={!dirty || busy !== null}
                onSave={(message) => act("Save version")(() => save(message))}
              />
              <Button
                size="sm"
                disabled={busy !== null}
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
              <DropdownMenuItem
                onSelect={() => {
                  setAsking({ anchor: null })
                  setTab("comments")
                }}
              >
                <SparkleIcon size={14} /> Ask the agent for a change
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={openComments.length === 0}
                onSelect={() => act("Ask the agent")(addressComments)}
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
              <span className="flex items-center gap-space-3">
                This draft has commits made outside Docs, such as by the agent.
                <Button
                  size="xs"
                  variant="outlined"
                  onClick={() => act("Load commits")(loadOutsideCommits)}
                >
                  Load them
                </Button>
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
            // Until the draft's live document syncs, the page stays as it was.
            live={liveShown}
            me={me}
            highlights={highlights}
            activeId={activeId}
            actions={{
              comment: (anchor) => {
                setComposing({ anchor, kind: "comment" })
                setTab("comments")
              },
              suggest: (anchor) => {
                setComposing({ anchor, kind: "suggestion" })
                setTab("comments")
              },
              askAgent: (anchor) => {
                setAsking({ anchor })
                setTab("comments")
              },
            }}
            onSelectComment={(id) => {
              setActiveId(id)
              setTab("comments")
            }}
            onLink={onLink}
            onReady={(ready) => {
              editor.current = ready
              setSaved(normalize(ready, split.body))
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
      <aside className="flex w-80 shrink-0 flex-col border-l border-default max-lg:hidden">
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
        </nav>
        {tab === "comments" && asking && (
          <AskAgent
            anchor={asking.anchor}
            onCancel={() => setAsking(null)}
            onSubmit={(instruction) => {
              const anchor = asking.anchor
              setAsking(null)
              act("Ask the agent")(() => askAgent(instruction, anchor))
            }}
          />
        )}
        {tab === "comments" ? (
          <CommentsPanel
            threads={threads}
            people={people}
            composing={composing}
            activeId={activeId}
            canAccept
            act={act("Update comment")}
            onCompose={async (body, suggestion) => {
              if (!composing) return
              await putComment(
                crypto.randomUUID(),
                newComment({
                  parent: null,
                  ...composing.anchor,
                  body,
                  suggestion,
                })
              )
              setComposing(null)
            }}
            onCancelCompose={() => setComposing(null)}
            onSelect={setActiveId}
            onReply={(parent, body) =>
              putComment(
                crypto.randomUUID(),
                newComment({
                  parent,
                  quote: "",
                  prefix: "",
                  suffix: "",
                  body,
                  suggestion: null,
                })
              )
            }
            onSetStatus={setStatus}
            onAccept={async (thread) => {
              if (live && synced) return applySuggestion(thread)
              await ensureDraft()
              setPendingAccept(thread.id)
            }}
          />
        ) : (
          <Activity repository={repository} path={path} gitRef={ref} />
        )}
      </aside>
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
  const threads = useThreads(true)
  const mine = (threads.data ?? []).filter(
    (thread) => thread.title === `Docs: ${pageTitle(path)}`
  )
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
      {mine.length > 0 && (
        <section className="flex flex-col gap-space-2">
          <h3 className="flex items-center gap-space-1 text-xs font-medium text-secondary">
            <SparkleIcon size={12} /> Agent threads
          </h3>
          {mine.map((thread) => (
            <button
              key={thread.threadId}
              type="button"
              onClick={() => void host.nav.openThread(thread.threadId)}
              className="flex items-center justify-between rounded-md border border-default px-space-2 py-space-1 text-left text-sm text-primary hover:bg-surface-level-2-hover"
            >
              <span className="truncate">{thread.title}</span>
              <Badge size="xxs" color="secondary">
                {thread.status}
              </Badge>
            </button>
          ))}
        </section>
      )}
    </div>
  )
}

/** The request to the agent, inline at the top of the side panel. */
function AskAgent({
  anchor,
  onCancel,
  onSubmit,
}: {
  anchor: Anchor | null
  onCancel: () => void
  onSubmit: (instruction: string) => void
}) {
  const [instruction, setInstruction] = useState("")
  return (
    <form
      className="m-space-3 mb-0 flex flex-col gap-space-2 rounded-lg border border-default bg-surface-level-1 p-space-3"
      onSubmit={(event) => {
        event.preventDefault()
        if (instruction.trim()) onSubmit(instruction.trim())
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") onCancel()
      }}
    >
      <span className="flex items-center gap-space-1 text-xs font-medium text-secondary">
        <SparkleIcon size={12} /> Ask the agent
      </span>
      {anchor && (
        <blockquote className="line-clamp-3 border-l-2 border-default pl-space-2 text-xs text-secondary">
          {anchor.quote}
        </blockquote>
      )}
      <Textarea
        placeholder={
          anchor
            ? "What should change here?"
            : "What should change on this page?"
        }
        value={instruction}
        onChange={setInstruction}
        rows={3}
        autoFocus
      />
      <p className="text-xs text-tertiary">
        It edits this page on its draft and commits there; you&apos;ll see the
        request before it starts.
      </p>
      <div className="flex justify-end gap-space-2">
        <Button type="button" size="sm" variant="plain" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={!instruction.trim()}>
          Continue
        </Button>
      </div>
    </form>
  )
}
