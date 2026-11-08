import type { WebMCP } from "webmcp-types"

/** WebMCP's rule for tool names: 1–128 ASCII letters, digits, `_`, `-` or `.`. */
const TOOL_NAME = /^[A-Za-z0-9_.-]{1,128}$/

interface Registration {
  tool: WebMCP.ModelContextTool
  described: WebMCP.RegisteredTool
}

/** The `toolactivated` and `toolcancel` events, which name the tool. */
class ToolEvent extends Event {
  constructor(
    type: "toolactivated" | "toolcancel",
    readonly toolName: string
  ) {
    super(type)
  }
}

/**
 * A WebMCP `ModelContext` that belongs to Robin's own chat, not to the browser.
 *
 * It follows the WebMCP draft's interface (`registerTool`, `getTools`,
 * `executeTool` and the `toolchange` event), so the dashboard's tools are
 * written to the standard contract. It is never installed as
 * `document.modelContext`: no browser agent or extension can see or call
 * these tools, only the code that holds this instance.
 */
export class PageModelContext
  extends EventTarget
  implements WebMCP.ModelContext
{
  readonly #tools = new Map<string, Registration>()
  #handlers: Partial<Record<keyof WebMCP.ModelContextEventMap, EventListener>> =
    {}

  /**
   * Adds a tool until `options.signal` aborts. Rejects with `InvalidStateError`
   * for a name WebMCP disallows, an empty description or a name already taken.
   */
  registerTool(
    tool: WebMCP.ModelContextTool,
    options: WebMCP.ModelContextRegisterToolOptions = {}
  ): Promise<void> {
    const { signal } = options
    if (signal?.aborted) return Promise.reject(signal.reason)
    if (!TOOL_NAME.test(tool.name))
      return Promise.reject(
        new DOMException(
          `Invalid tool name "${tool.name}"`,
          "InvalidStateError"
        )
      )
    if (!tool.description)
      return Promise.reject(
        new DOMException(
          `Tool "${tool.name}" has no description`,
          "InvalidStateError"
        )
      )
    if (this.#tools.has(tool.name))
      return Promise.reject(
        new DOMException(
          `A tool named "${tool.name}" is already registered`,
          "InvalidStateError"
        )
      )
    let inputSchema: object | undefined
    try {
      inputSchema = tool.inputSchema && structuredClone(tool.inputSchema)
    } catch (error) {
      return Promise.reject(error)
    }
    const registration: Registration = {
      tool,
      described: {
        name: tool.name,
        title: tool.title ?? "",
        description: tool.description,
        ...(inputSchema ? { inputSchema } : {}),
        ...(tool.annotations ? { annotations: { ...tool.annotations } } : {}),
        window,
        origin: window.location.origin,
      },
    }
    // Registered synchronously, so an unmount and remount in one commit
    // (StrictMode, a key change) always frees the name before taking it again.
    this.#tools.set(tool.name, registration)
    signal?.addEventListener(
      "abort",
      () => {
        if (this.#tools.get(tool.name) !== registration) return
        this.#tools.delete(tool.name)
        this.dispatchEvent(new Event("toolchange"))
      },
      { once: true }
    )
    this.dispatchEvent(new Event("toolchange"))
    return Promise.resolve()
  }

  /** The tools registered now, in registration order. */
  getTools(
    _options?: WebMCP.ModelContextGetToolOptions
  ): Promise<Array<WebMCP.RegisteredTool>> {
    return Promise.resolve(
      Array.from(this.#tools.values(), ({ described }) => described)
    )
  }

  /**
   * Runs a registered tool and resolves with its result as JSON, as WebMCP's
   * `executeTool` does. Rejects with `NotFoundError` for a tool no longer
   * registered and `OperationError` when the tool throws or returns something
   * JSON cannot hold.
   */
  async executeTool(
    tool: WebMCP.RegisteredTool,
    inputObject: object = {},
    options: WebMCP.ModelContextExecuteToolOptions = {}
  ): Promise<string> {
    const registration = this.#tools.get(tool.name)
    if (!registration)
      throw new DOMException(
        `No tool named "${tool.name}" is registered`,
        "NotFoundError"
      )
    const signal = options.signal ?? new AbortController().signal
    signal.throwIfAborted()
    const cancel = () =>
      this.dispatchEvent(new ToolEvent("toolcancel", tool.name))
    signal.addEventListener("abort", cancel, { once: true })
    this.dispatchEvent(new ToolEvent("toolactivated", tool.name))
    let result: unknown
    try {
      result = await registration.tool.execute(
        inputObject as Record<string, unknown>,
        { signal }
      )
    } catch (error) {
      throw new DOMException(
        error instanceof Error ? error.message : String(error),
        "OperationError"
      )
    } finally {
      signal.removeEventListener("abort", cancel)
    }
    signal.throwIfAborted()
    let json: string | undefined
    try {
      json = JSON.stringify(result ?? null)
    } catch {
      json = undefined
    }
    if (json === undefined)
      throw new DOMException(
        `Tool "${tool.name}" returned a result JSON cannot hold`,
        "OperationError"
      )
    return json
  }

  get ontoolchange() {
    return this.#handler("toolchange")
  }
  set ontoolchange(
    handler: ((this: WebMCP.ModelContext, ev: Event) => unknown) | null
  ) {
    this.#setHandler("toolchange", handler)
  }
  get ontoolactivated() {
    return this.#handler("toolactivated")
  }
  set ontoolactivated(
    handler:
      | ((this: WebMCP.ModelContext, ev: WebMCP.ToolActivatedEvent) => unknown)
      | null
  ) {
    this.#setHandler("toolactivated", handler)
  }
  get ontoolcancel() {
    return this.#handler("toolcancel")
  }
  set ontoolcancel(
    handler:
      | ((this: WebMCP.ModelContext, ev: WebMCP.ToolCancelEvent) => unknown)
      | null
  ) {
    this.#setHandler("toolcancel", handler)
  }

  #handler(type: keyof WebMCP.ModelContextEventMap) {
    return (this.#handlers[type] as never) ?? null
  }

  #setHandler(
    type: keyof WebMCP.ModelContextEventMap,
    handler: ((this: WebMCP.ModelContext, ev: never) => unknown) | null
  ) {
    const previous = this.#handlers[type]
    if (previous) this.removeEventListener(type, previous)
    if (!handler) {
      delete this.#handlers[type]
      return
    }
    const listener = handler as unknown as EventListener
    this.#handlers[type] = listener
    this.addEventListener(type, listener)
  }
}
