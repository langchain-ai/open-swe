import type { JsonObject } from "@openswe/plugin-sdk"
import { usePluginHost } from "@openswe/plugin-sdk/react"
import { useNavigate } from "@tanstack/react-router"
import {
  createContext,
  useContext,
  useEffect,
  useEffectEvent,
  useRef,
} from "react"
import type { RefObject } from "react"

import type { Draft } from "@/lib/model"
import { isMarkdown } from "@/lib/model"

/** What the page on screen lets the agent do; the repository's tools reach it through this. */
export interface PageTools {
  read: () => JsonObject
  edit: (find: string, replace: string) => Promise<JsonObject>
  comment: (
    quote: string,
    body: string,
    suggestion: string | null
  ) => Promise<JsonObject>
}

export const OpenPage = createContext<RefObject<PageTools | null> | null>(null)

const INSTRUCTIONS = `Docs shows the Markdown pages of one repository and lets people edit them together live.
- read_page returns the page the person has open, what they selected, and its open comments; read it before changing anything.
- edit_page changes the open page in its draft, which everyone viewing sees as it happens. Use it rather than editing the file in the repository: the person saves versions and publishes the draft as a pull request from Docs themselves.
- add_comment leaves a comment, or a suggested replacement the person can accept.
- open_page opens another page for the person.`

const NO_ARGUMENTS = { type: "object", properties: {} }

function page(ref: RefObject<PageTools | null>): PageTools {
  if (!ref.current)
    throw new Error("No page is open; list_pages and open_page open one.")
  return ref.current
}

function text(args: JsonObject, name: string): string {
  const value = args[name]
  if (typeof value !== "string") throw new Error(`${name} must be a string`)
  return value
}

/**
 * Gives the agent in the dock this repository's tools while it is open, and
 * points the dock at the person's chat about it.
 */
export function useAgentTools(
  repository: string,
  pages: ReadonlyArray<string>,
  drafts: ReadonlyMap<string, Draft>
) {
  const host = usePluginHost()
  const navigate = useNavigate()
  const open = useRef<PageTools | null>(null)
  const listPages = useEffectEvent(() => ({
    pages: [...new Set([...pages, ...drafts.keys()])].sort(),
    drafts: [...drafts.keys()],
  }))
  const openPage = useEffectEvent((path: string) => {
    if (!isMarkdown(path)) throw new Error(`${path} is not a Markdown page`)
    const [owner = "", repo = ""] = repository.split("/")
    void navigate({
      to: "/$owner/$repo/$",
      params: { owner, repo, _splat: path },
    })
    return { opened: path }
  })

  useEffect(() => {
    void host.agent.use({ name: "repository", repository })
    return host.agent.provide(
      {
        list_pages: {
          description:
            "The repository's Markdown pages, and which of them have a draft open.",
          parameters: NO_ARGUMENTS,
          run: () => listPages(),
        },
        open_page: {
          description:
            "Opens a page for the person, by its path in the repository.",
          parameters: {
            type: "object",
            properties: {
              path: { type: "string", description: "Such as docs/intro.md" },
            },
            required: ["path"],
          },
          run: (args) => openPage(text(args, "path")),
        },
        read_page: {
          description:
            "The open page: its path, whether it has a draft, its Markdown, the text the person selected, and its open comments.",
          parameters: NO_ARGUMENTS,
          run: () => page(open).read(),
        },
        edit_page: {
          description:
            "Replaces the first passage that reads `find` on the open page with `replace`, in its draft, starting one if there is none. `find` is the text as it reads on the page, without Markdown syntax; `replace` is Markdown.",
          parameters: {
            type: "object",
            properties: {
              find: { type: "string" },
              replace: { type: "string" },
            },
            required: ["find", "replace"],
          },
          run: (args) =>
            page(open).edit(text(args, "find"), text(args, "replace")),
        },
        add_comment: {
          description:
            "Comments on a passage of the open page, as the person. With `suggestion`, it suggests replacing the passage with that text, which the person can accept.",
          parameters: {
            type: "object",
            properties: {
              quote: {
                type: "string",
                description: "The passage as it reads on the page.",
              },
              body: { type: "string" },
              suggestion: { type: "string" },
            },
            required: ["quote", "body"],
          },
          run: (args) =>
            page(open).comment(
              text(args, "quote"),
              text(args, "body"),
              typeof args.suggestion === "string" ? args.suggestion : null
            ),
        },
      },
      INSTRUCTIONS
    )
  }, [host, repository])

  return open
}

/** Lets the agent's page tools reach `tools` while this page is open. */
export function useOpenPageTools(tools: PageTools) {
  const ref = useContext(OpenPage)
  const read = useEffectEvent(() => tools.read())
  const edit = useEffectEvent((find: string, replace: string) =>
    tools.edit(find, replace)
  )
  const comment = useEffectEvent(
    (quote: string, body: string, suggestion: string | null) =>
      tools.comment(quote, body, suggestion)
  )
  useEffect(() => {
    if (!ref) return
    const mine: PageTools = { read, edit, comment }
    ref.current = mine
    return () => {
      if (ref.current === mine) ref.current = null
    }
  }, [ref])
}

/**
 * Tells the agent what the person has open, so each message they send from
 * the dock carries it, labelled `label` in the thread; it is shared again
 * whenever it changes.
 */
export function useSharedContext(context: JsonObject, label: string) {
  const host = usePluginHost()
  const json = JSON.stringify(context)
  useEffect(() => {
    void host.agent.share(JSON.parse(json) as JsonObject, { label })
  }, [host, json, label])
}
