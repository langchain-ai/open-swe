import { useQuery } from "@tanstack/react-query"
import { useEffect, useSyncExternalStore } from "react"
import type { WebMCP } from "webmcp-types"

import { agentsApi } from "@/features/agents/lib/api"
import type { PageCommand, PageToolOffer } from "@/features/agents/lib/api"
import { reportError } from "@/lib/errorReporting"
import { usePageModelContext } from "@/lib/pageTools"
import type { PageModelContext } from "@/lib/pageTools"

/** Names the backend can keep apart from every other tool under its `page_` prefix. */
const OFFERABLE_NAME = /^[A-Za-z][A-Za-z0-9_-]{0,58}$/
const MAX_OFFERED = 32
const MAX_RESULT_CHARS = 64_000
const NO_INPUT = { type: "object", properties: {} }

const bridges = new WeakMap<PageModelContext, PageToolBridge>()

/**
 * Robin's chat as the agent of the page's model context.
 *
 * This browser tab is one page session. Each message sent from it carries the
 * page's tools and the session's id; Robin's calls to those tools queue
 * commands for the session, which this tab carries out in order and records
 * the results of. No other tab has the session, so none runs its commands.
 */
export class PageToolBridge {
  /** This tab's page session. */
  readonly session = crypto.randomUUID()
  readonly #context: PageModelContext
  readonly #listeners = new Set<() => void>()
  #sent = false
  /** The last position this tab carried out, so a refetch never runs one twice. */
  #executedThrough = 0
  #running: Promise<void> = Promise.resolve()

  private constructor(context: PageModelContext) {
    this.#context = context
  }

  static of(context: PageModelContext): PageToolBridge {
    let bridge = bridges.get(context)
    if (!bridge) {
      bridge = new PageToolBridge(context)
      bridges.set(context, bridge)
    }
    return bridge
  }

  /** The tools the page offers now, as a message's `page_tools`. */
  async offer(): Promise<Array<PageToolOffer>> {
    const tools = await this.#context.getTools()
    const offered: Array<PageToolOffer> = []
    for (const tool of tools) {
      if (!OFFERABLE_NAME.test(tool.name)) {
        console.error(`Page tool "${tool.name}" has a name Robin cannot offer`)
        continue
      }
      if (offered.length === MAX_OFFERED) {
        console.error(`The page offers more than ${MAX_OFFERED} tools`)
        break
      }
      offered.push({
        name: tool.name,
        description: tool.title
          ? `${tool.title}: ${tool.description}`
          : tool.description,
        parameters: tool.inputSchema ?? NO_INPUT,
      })
    }
    return offered
  }

  /** A message carried this session's tools, so commands may be queued for it from now on. */
  sent(): void {
    if (this.#sent) return
    this.#sent = true
    for (const listener of this.#listeners) listener()
  }

  /** Whether a message carried this session's tools yet. */
  get hasSent(): boolean {
    return this.#sent
  }

  subscribe(listener: () => void): () => void {
    this.#listeners.add(listener)
    return () => this.#listeners.delete(listener)
  }

  /**
   * Carries out the waiting commands it has not run yet, one at a time and in
   * order, recording each result before the next starts. Calls queue behind
   * each other, so a refetch mid-run picks up where the last one stops.
   */
  run(commands: ReadonlyArray<PageCommand>): Promise<void> {
    // A failed run is reported to its caller; the next one still starts.
    this.#running = this.#running
      .catch(() => undefined)
      .then(async () => {
        for (const command of commands) {
          if (command.position <= this.#executedThrough) continue
          const result = await this.#execute(command)
          await agentsApi.recordPageCommand(
            this.session,
            command.position,
            result
          )
          this.#executedThrough = command.position
        }
      })
    return this.#running
  }

  async #execute(command: PageCommand): Promise<unknown> {
    const tools = await this.#context.getTools()
    const tool = tools.find(
      (candidate: WebMCP.RegisteredTool) => candidate.name === command.tool
    )
    if (!tool)
      return {
        error: `The page no longer offers ${command.tool}; what is on screen changed since it was queued.`,
      }
    let output: string
    try {
      output = await this.#context.executeTool(tool, command.arguments)
    } catch (error) {
      return { error: error instanceof Error ? error.message : String(error) }
    }
    if (output.length > MAX_RESULT_CHARS)
      return {
        error: `The result was longer than ${MAX_RESULT_CHARS} characters.`,
      }
    return JSON.parse(output) as unknown
  }
}

/** The bridge between Robin's chat and the page's tools. */
export function usePageToolBridge(): PageToolBridge {
  return PageToolBridge.of(usePageModelContext())
}

/**
 * Carries out the commands Robin queues for this tab's page session, from the
 * first message it sends with its tools on. Mounted once for the whole app,
 * so commands still run after one of them moves the person to another page.
 */
export function usePageCommandRunner(): void {
  const bridge = usePageToolBridge()
  const sent = useSyncExternalStore(
    (listener) => bridge.subscribe(listener),
    () => bridge.hasSent,
    () => false
  )
  const queue = useQuery({
    queryKey: ["page-commands", bridge.session],
    queryFn: () => agentsApi.pageCommands(bridge.session),
    enabled: sent,
    meta: {
      invalidatedBy: (data) =>
        data && typeof data === "object" && "topic" in data
          ? [String(data.topic)]
          : [],
    },
  })
  const commands = queue.data?.commands
  useEffect(() => {
    if (!commands?.length) return
    bridge.run(commands).catch((error: unknown) => {
      reportError({
        title: "Couldn't carry out Robin's change to the page",
        error,
      })
    })
  }, [bridge, commands])
}
