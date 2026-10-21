import type {
  AgentThread,
  PullRequest,
  StoredDocument,
} from "@openswe/plugin-sdk"
import type { Node as ProseMirrorNode } from "@tiptap/pm/model"
import type { Editor } from "@tiptap/react"
import { useEffect, useMemo, useRef, useState } from "react"

import { resolveAnchor, type Highlight } from "./anchors"
import { CommentsPanel, type CommentThread, type Composing } from "./Comments"
import { PageEditor, type SelectionAction } from "./Editor"
import { errorMessage, useHost, useHostContext } from "./host"
import {
  COMMENTS,
  DRAFTS,
  draftBranch,
  newId,
  pageTitle,
  splitFrontMatter,
  timeAgo,
  withTrailingNewline,
  type Anchor,
  type CommentStatus,
  type Draft,
  type PageComment,
} from "./model"
import { useLoad, useSpace } from "./space"

type Tab = "comments" | "history" | "agent"

interface Loaded {
  ref: string
  head: string
  /** The file at `head`; `null` when the page does not exist there yet. */
  text: string | null
}

export function PageView({ path }: { path: string }) {
  const host = useHost()
  const { user } = useHostContext()
  const space = useSpace()
  const github = useMemo(
    () => host.github(space.repository),
    [host, space.repository]
  )
  const draft = space.drafts.get(path) ?? null
  const ref = draft?.branch ?? space.defaultBranch
  const editing = draft !== null

  const file = useLoad(async (): Promise<Loaded> => {
    const head = await github.ref(ref)
    if (!head) throw new Error(`Branch ${ref} no longer exists`)
    return { ref, head, text: await github.file(path, head) }
  }, [github, path, ref])
  const loaded = file.data ?? null
  const load = file.reload

  const editor = useRef<Editor | null>(null)
  const [baseline, setBaseline] = useState<string | null>(null)
  const [current, setCurrent] = useState<string | null>(null)
  const [source, setSource] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>("comments")
  const [composing, setComposing] = useState<Composing | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [message, setMessage] = useState("")

  const split = useMemo(() => splitFrontMatter(loaded?.text ?? ""), [loaded])
  const dirty =
    source !== null
      ? source !== baseline
      : current !== null && baseline !== null && current !== baseline

  // Pick up commits someone else, or an agent, pushed to the draft.
  useEffect(() => {
    const onFocus = () => {
      if (!loaded || dirty) return
      void github.ref(ref).then((head) => {
        if (head && head !== loaded.head) load()
      })
    }
    window.addEventListener("focus", onFocus)
    return () => window.removeEventListener("focus", onFocus)
  }, [dirty, github, load, loaded, ref])

  const comments = useLoad(
    async () =>
      (await host.storage.query<PageComment>(COMMENTS, {
        repository: space.repository,
        where: { path },
        limit: 500,
      })) as StoredDocument<PageComment>[],
    [host, space.repository, path, space.changes]
  )

  const pull = useLoad(
    async (): Promise<PullRequest | null> =>
      draft ? github.findPull(draft.branch) : null,
    [github, draft?.branch]
  )

  const [doc, setDoc] = useState<ProseMirrorNode | null>(null)
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

  const run = async (label: string, work: () => Promise<void>) => {
    setBusy(label)
    try {
      await work()
    } catch (error) {
      void host.ui.toast({
        title: `Couldn't ${label.toLowerCase()}`,
        description: errorMessage(error),
        intent: "error",
      })
    } finally {
      setBusy(null)
    }
  }

  const ensureDraft = async (): Promise<Draft> => {
    if (draft) return draft
    const base = await github.ref(space.defaultBranch)
    if (!base) throw new Error(`${space.defaultBranch} no longer exists`)
    const started: Draft = {
      branch: draftBranch(path),
      base: space.defaultBranch,
      startedBy: user.login,
      startedAt: new Date().toISOString(),
    }
    await github.createBranch(started.branch, base)
    await host.storage.put(DRAFTS, path, started, {
      repository: space.repository,
    })
    space.reloadDrafts()
    editor.current?.setEditable(true)
    return started
  }

  const markdownNow = (): string =>
    source ?? editor.current?.getMarkdown() ?? current ?? ""

  const save = async (): Promise<string | null> => {
    if (!loaded || !draft) return null
    const text = split.frontMatter + withTrailingNewline(markdownNow())
    const head = await github.commit({
      branch: draft.branch,
      expectedHead: loaded.head,
      message: message.trim() || `Update ${path}`,
      files: { [path]: text },
    })
    setSource(null)
    setMessage("")
    load()
    return head
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
        title: `${loaded?.text === null ? "Add" : "Update"} ${pageTitle(path)}`,
        body: `Published from Spaces.\n\nPage: \`${path}\``,
      }))
    pull.reload()
    void host.ui.toast({
      title: existing
        ? `Pull request #${opened.number} is open`
        : `Opened pull request #${opened.number}`,
      intent: "success",
    })
  }

  const closeDraft = async () => {
    await host.storage.delete(DRAFTS, path, { repository: space.repository })
    setSource(null)
    space.reloadDrafts()
  }

  const discardChanges = () => {
    setSource(null)
    if (baseline !== null) {
      editor.current?.commands.setContent(baseline, { contentType: "markdown" })
      setCurrent(baseline)
    }
  }

  const putComment = (id: string, comment: PageComment) =>
    host.storage.put(COMMENTS, id, comment, { repository: space.repository })

  const compose = async (body: string, suggestion: string | null) => {
    if (!composing) return
    await putComment(newId(), {
      path,
      parent: null,
      ...composing.anchor,
      body,
      suggestion,
      author: user.login,
      createdAt: new Date().toISOString(),
      status: "open",
    })
    setComposing(null)
    setTab("comments")
    comments.reload()
  }

  const setStatus = async (id: string, status: CommentStatus) => {
    const thread = threads.find((t) => t.id === id)
    if (!thread) return
    await putComment(id, { ...thread.comment, status })
    comments.reload()
  }

  const accept = async (thread: CommentThread) => {
    await ensureDraft()
    const view = editor.current
    const range = view && resolveAnchor(view.state.doc, thread.comment)
    if (!view || !range || thread.comment.suggestion === null) return
    view.setEditable(true)
    view
      .chain()
      .focus()
      .insertContentAt(range, {
        type: "text",
        text: thread.comment.suggestion,
      })
      .run()
    await setStatus(thread.id, "accepted")
  }

  const askAgent = async (instruction: string, anchor: Anchor | null) => {
    const active = await ensureDraft()
    if (dirty) await save()
    const request = [
      `Edit \`${path}\` in ${space.repository} on the existing branch \`${active.branch}\`.`,
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
      repository: space.repository,
      title: `Spaces: ${pageTitle(path)}`,
      request,
    })
    if (started) {
      setTab("agent")
      void host.ui.toast({
        title: "Agent thread started",
        description: "Its commits land on this page's draft.",
        intent: "success",
      })
    }
  }

  const addressComments = () => {
    const open = threads.filter((t) => t.comment.status === "open")
    const lines = open.map(({ comment }) =>
      comment.suggestion !== null
        ? `- Replace "${comment.quote}" with "${comment.suggestion}"${comment.body ? ` (${comment.body})` : ""}`
        : `- On "${comment.quote}": ${comment.body}`
    )
    return askAgent(
      ["Address these review comments on the page:", "", ...lines].join("\n"),
      null
    )
  }

  const [agentPrompt, setAgentPrompt] = useState<{
    anchor: Anchor | null
  } | null>(null)
  const startComment = (anchor: Anchor, kind: Composing["kind"]) => {
    setComposing({ anchor, kind })
    setTab("comments")
  }
  const actions: SelectionAction[] = [
    { label: "Comment", run: (anchor) => startComment(anchor, "comment") },
    {
      label: "Suggest edit",
      run: (anchor) => startComment(anchor, "suggestion"),
    },
    { label: "Ask agent", run: (anchor) => setAgentPrompt({ anchor }) },
  ]

  const toggleSource = () => {
    if (source === null) return setSource(markdownNow())
    // Back to rich text: the editor takes the Markdown as it was written.
    editor.current?.commands.setContent(source, { contentType: "markdown" })
    setCurrent(editor.current?.getMarkdown() ?? source)
    setSource(null)
  }

  if (file.error && !loaded)
    return (
      <div className="center">
        <p className="error">{file.error}</p>
        <button type="button" onClick={load}>
          Try again
        </button>
      </div>
    )
  if (!loaded) return <div className="center muted">Loading {path}…</div>

  const missing = loaded.text === null
  const openCount = threads.filter((t) => t.comment.status === "open").length

  return (
    <div className="page">
      <div className="page-main">
        <header className="page-header">
          <div className="crumbs">
            <span className="muted">
              {path.split("/").slice(0, -1).join(" / ")}
            </span>
          </div>
          <div className="row wrap">
            <span className={`chip ${editing ? "chip-draft" : ""}`}>
              {editing
                ? `Draft · ${draft.branch}`
                : `Published · ${space.defaultBranch}`}
            </span>
            {dirty && (
              <>
                <span className="chip chip-dirty">Unsaved changes</span>
                <button type="button" className="link" onClick={discardChanges}>
                  Discard
                </button>
              </>
            )}
            {pull.data && (
              <button
                type="button"
                className="link"
                onClick={() => void host.nav.openExternal(pull.data!.url)}
              >
                Pull request #{pull.data.number}
              </button>
            )}
            <span className="spacer" />
            {editing ? (
              <>
                <button type="button" className="ghost" onClick={toggleSource}>
                  {source === null ? "Markdown" : "Rich text"}
                </button>
                <input
                  className="message"
                  placeholder="Describe this version"
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                />
                <button
                  type="button"
                  disabled={!dirty || busy !== null}
                  onClick={() =>
                    void run("Save version", async () => {
                      await save()
                    })
                  }
                >
                  {busy === "Save version" ? "Saving…" : "Save version"}
                </button>
                <button
                  type="button"
                  className="primary"
                  disabled={busy !== null}
                  onClick={() => void run("Publish", publish)}
                >
                  {pull.data ? "Update pull request" : "Publish"}
                </button>
                <button
                  type="button"
                  className="ghost"
                  disabled={busy !== null || dirty}
                  title={
                    dirty
                      ? "Save or discard your changes first"
                      : "Go back to the published page; the branch stays on GitHub"
                  }
                  onClick={() => void run("Close draft", closeDraft)}
                >
                  Close draft
                </button>
              </>
            ) : (
              <button
                type="button"
                className="primary"
                disabled={busy !== null}
                onClick={() =>
                  void run("Start editing", async () => {
                    await ensureDraft()
                  })
                }
              >
                {busy === "Start editing" ? "Starting…" : "Edit"}
              </button>
            )}
          </div>
          <h1 className="page-title">{pageTitle(path)}</h1>
          {editing && draft.startedBy !== user.login && (
            <p className="muted">
              {draft.startedBy} started this draft {timeAgo(draft.startedAt)}.
            </p>
          )}
          {missing && (
            <p className="muted">
              This page isn&apos;t on {loaded.ref} yet.{" "}
              {editing
                ? "Write it, then save a version."
                : "Edit it to create it."}
            </p>
          )}
        </header>
        {source !== null && (
          <textarea
            className="source"
            value={source}
            onChange={(event) => setSource(event.target.value)}
            spellCheck={false}
          />
        )}
        <div hidden={source !== null}>
          <PageEditor
            markdown={split.body}
            editable={editing}
            highlights={highlights}
            activeId={activeId}
            actions={actions}
            onSelectComment={(id) => {
              setActiveId(id)
              setTab("comments")
            }}
            onReady={(ready) => {
              editor.current = ready
              const normalized = ready.getMarkdown()
              setBaseline(normalized)
              setCurrent(normalized)
              setDoc(ready.state.doc)
            }}
            onChange={(markdown, changed) => {
              setCurrent(markdown)
              setDoc(changed)
            }}
          />
        </div>
      </div>
      <aside className="panel">
        <nav className="tabs">
          <button
            type="button"
            className={tab === "comments" ? "on" : ""}
            onClick={() => setTab("comments")}
          >
            Comments{openCount ? ` ${openCount}` : ""}
          </button>
          <button
            type="button"
            className={tab === "history" ? "on" : ""}
            onClick={() => setTab("history")}
          >
            History
          </button>
          <button
            type="button"
            className={tab === "agent" ? "on" : ""}
            onClick={() => setTab("agent")}
          >
            Agent
          </button>
        </nav>
        {tab === "comments" && (
          <CommentsPanel
            threads={threads}
            composing={composing}
            activeId={activeId}
            canAccept={source === null}
            onCompose={(body, suggestion) =>
              run("Add comment", () => compose(body, suggestion))
            }
            onCancelCompose={() => setComposing(null)}
            onSelect={setActiveId}
            onReply={(parent, body) =>
              run("Reply", async () => {
                await putComment(newId(), {
                  path,
                  parent,
                  quote: "",
                  prefix: "",
                  suffix: "",
                  body,
                  suggestion: null,
                  author: user.login,
                  createdAt: new Date().toISOString(),
                  status: "open",
                })
                comments.reload()
              })
            }
            onSetStatus={(id, status) =>
              run("Update comment", () => setStatus(id, status))
            }
            onAccept={(thread) =>
              run("Accept suggestion", () => accept(thread))
            }
          />
        )}
        {tab === "history" && (
          <History path={path} gitRef={loaded.ref} head={loaded.head} />
        )}
        {tab === "agent" && (
          <AgentPanel
            path={path}
            openComments={openCount}
            busy={busy !== null}
            onAsk={() => setAgentPrompt({ anchor: null })}
            onAddressComments={() => void run("Ask the agent", addressComments)}
            onReload={load}
          />
        )}
      </aside>
      {agentPrompt && (
        <AgentPrompt
          anchor={agentPrompt.anchor}
          onCancel={() => setAgentPrompt(null)}
          onSubmit={(instruction) => {
            setAgentPrompt(null)
            void run("Ask the agent", () =>
              askAgent(instruction, agentPrompt.anchor)
            )
          }}
        />
      )}
    </div>
  )
}

function History({
  path,
  gitRef,
  head,
}: {
  path: string
  gitRef: string
  head: string
}) {
  const host = useHost()
  const space = useSpace()
  const history = useLoad(
    () => host.github(space.repository).history(path, gitRef, 30),
    [host, space.repository, path, gitRef, head]
  )
  if (history.error) return <p className="panel-body error">{history.error}</p>
  if (!history.data) return <p className="panel-body muted">Loading…</p>
  if (history.data.length === 0)
    return <p className="panel-body muted">No versions on {gitRef} yet.</p>
  return (
    <ol className="panel-body history">
      {history.data.map((commit) => (
        <li key={commit.sha}>
          <button
            type="button"
            className="link"
            onClick={() =>
              void host.nav.openExternal(
                `https://github.com/${space.repository}/commit/${commit.sha}`
              )
            }
          >
            {commit.message.split("\n")[0]}
          </button>
          <span className="muted">
            {commit.authorLogin ?? commit.authorName}
            {commit.authoredAt ? ` · ${timeAgo(commit.authoredAt)}` : ""} ·{" "}
            {commit.sha.slice(0, 7)}
          </span>
        </li>
      ))}
    </ol>
  )
}

function AgentPanel({
  path,
  openComments,
  busy,
  onAsk,
  onAddressComments,
  onReload,
}: {
  path: string
  openComments: number
  busy: boolean
  onAsk: () => void
  onAddressComments: () => void
  onReload: () => void
}) {
  const host = useHost()
  const threads = useLoad(() => host.threads.list(), [host])
  const mine = (threads.data ?? []).filter((thread: AgentThread) =>
    thread.title.startsWith(`Spaces: ${pageTitle(path)}`)
  )
  return (
    <div className="panel-body">
      <p className="muted">
        The agent edits this page on its draft branch. You approve each request
        before it starts.
      </p>
      <div className="row wrap">
        <button type="button" disabled={busy} onClick={onAsk}>
          Ask for a change
        </button>
        <button
          type="button"
          disabled={busy || openComments === 0}
          onClick={onAddressComments}
        >
          Address {openComments} open comment{openComments === 1 ? "" : "s"}
        </button>
      </div>
      <div className="row">
        <strong>Threads</strong>
        <span className="spacer" />
        <button
          type="button"
          className="ghost"
          onClick={() => {
            threads.reload()
            onReload()
          }}
        >
          Refresh
        </button>
      </div>
      {mine.length === 0 && <p className="muted">None for this page yet.</p>}
      {mine.map((thread) => (
        <button
          key={thread.threadId}
          type="button"
          className="card thread-link"
          onClick={() => void host.nav.openThread(thread.threadId)}
        >
          <span>{thread.title}</span>
          <span className="chip">{thread.status}</span>
        </button>
      ))}
    </div>
  )
}

function AgentPrompt({
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
    <div className="overlay" onClick={onCancel}>
      <form
        className="card dialog"
        onClick={(event) => event.stopPropagation()}
        onSubmit={(event) => {
          event.preventDefault()
          if (instruction.trim()) onSubmit(instruction.trim())
        }}
      >
        <strong>Ask the agent</strong>
        {anchor && <blockquote>{anchor.quote}</blockquote>}
        <textarea
          autoFocus
          rows={4}
          placeholder={
            anchor
              ? "What should change here?"
              : "What should change on this page?"
          }
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
        />
        <div className="row end">
          <button type="button" className="ghost" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="submit"
            className="primary"
            disabled={!instruction.trim()}
          >
            Continue
          </button>
        </div>
      </form>
    </div>
  )
}
