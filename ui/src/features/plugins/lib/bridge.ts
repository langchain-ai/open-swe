/**
 * The web UI's side of the plugin protocol: the capabilities a plugin's frame
 * reaches over Cap'n Web. Plugins are untrusted, so every argument is checked
 * for shape here, before anything else runs, and for permission again by the
 * backend, which is the authority. All state lives in `#private` fields:
 * Cap'n Web exposes a target's prototype methods and nothing else.
 */
import type {
  CollectionApi,
  ChatScope,
  CommitWire,
  CursorWire,
  DocumentScope,
  GitHubRepositoryApi,
  HostApi,
  HostContext,
  HostEvent,
  JsonObject,
  JsonScalar,
  JsonValue,
  PluginPermission,
  PullRequestDraft,
  QueryWire,
  RepositoryPickOptions,
  RoomApi,
  RoomEvent,
  RoomScope,
  SharedRepository,
  StoredDocument,
  StoredJson,
  Subscription,
  Toast,
} from "@openswe/plugin-sdk"
import { RpcTarget } from "capnweb"
import type { RpcStub } from "capnweb"

import type {
  DocumentTarget,
  InstalledPlugin,
  RoomTarget,
} from "@/features/plugins/lib/api"
import { pluginsApi } from "@/features/plugins/lib/api"
import { DashboardRequestError } from "@/lib/dashboard-fetch"

export class BridgeRequestError extends Error {
  constructor(
    message: string,
    /** An own enumerable property, so it reaches the plugin with the error. */
    readonly status: number
  ) {
    super(message)
    this.name = "BridgeRequestError"
  }
}

/**
 * What the plugin sees of a failure: its message and an HTTP-like status,
 * never a stack or anything else the error carried.
 */
export function sendableError(error: Error): Error {
  const status =
    error instanceof BridgeRequestError ||
    error instanceof DashboardRequestError
      ? error.status
      : null
  const sent = Object.assign(new Error(error.message), { status })
  delete sent.stack
  return sent
}

/** What a plugin offers the agent in its dock: its tools, and when to use them. */
export interface AgentToolOffer {
  tools: Array<{ name: string; description: string; parameters: JsonObject }>
  instructions: string
}

/** Runs one call in the plugin's frame; resolves to its result's text, or rejects with the plugin's error. */
export type ToolRunner = (tool: string, args: JsonObject) => Promise<string>

/** What a plugin says the person has open: JSON for the agent, and a label the thread shows. */
export interface SharedContext {
  json: string
  label: string | null
}

/** The chat the dock shows: the person's chat under `name`, about `repository` if any. */
export interface ChatTarget {
  name: string
  repository: string | null
}

export interface RepositoryPick {
  /** Lowercase `owner/repo` names the plugin would like chosen. */
  suggested: Array<string>
  multiple: boolean
}

/** What a plugin's requests may do in the web UI beyond calling the backend. */
export interface BridgeHost {
  plugin: InstalledPlugin
  context: () => Promise<HostContext>
  /** Calls `listener` as the theme or the plugin's path changes; returns the unsubscribe. */
  subscribe: (listener: (event: HostEvent) => void) => () => void
  navigate: (path: string, replace: boolean) => void
  openThread: (threadId: string) => void
  toast: (toast: Toast) => void
  /** The page's title, before " · Open SWE"; `null` is the plugin's name. */
  setTitle: (title: string | null) => void
  /** Asks before leaving for a site other than GitHub; resolves whether to go. */
  confirmLink: (url: string) => Promise<boolean>
  /** Shows the person a picker and shares what they choose; resolves to it, `[]` if they cancel. */
  pickRepositories: (pick: RepositoryPick) => Promise<Array<string>>
  /** Whether `owner/repo` is in one of this Open SWE's organizations; the plugin learns only this bool. */
  isInBaseOrg: (repository: string) => boolean
  /** Offers the agent in the dock these tools until the returned withdrawal. */
  provideTools: (offer: AgentToolOffer, run: ToolRunner) => () => void
  /** Chooses the chat the dock shows. */
  chooseChat: (chat: ChatTarget) => void
  /** Opens the dock, with `draft` in its composer. */
  openChat: (draft: string | null) => void
  /** What the person has open, as the plugin describes it, for the agent's next messages. */
  shareContext: (context: SharedContext) => void
  /** Calls `onChange` whenever anyone writes to `target`; returns the unsubscribe. */
  watchDocuments: (target: DocumentTarget, onChange: () => void) => () => void
  /** Streams one live room to `onEvent` from after `after`; returns the leave. */
  joinRoom: (
    target: RoomTarget,
    after: number,
    onEvent: (event: RoomEvent) => void
  ) => () => void
}

// ---- Shapes ---------------------------------------------------------------------

function invalid(message: string): never {
  throw new BridgeRequestError(message, 400)
}

function text(value: unknown, name: string, max = 1_000): string {
  if (typeof value !== "string" || !value || value.length > max)
    invalid(`${name} must be a non-empty string`)
  return value
}

function optionalText(value: unknown, name: string, max = 1_000) {
  return value === undefined || value === null
    ? undefined
    : text(value, name, max)
}

function optionalInteger(value: unknown, name: string): number | undefined {
  if (value === undefined) return undefined
  if (typeof value !== "number" || !Number.isInteger(value))
    invalid(`${name} must be an integer`)
  return value
}

function record(value: unknown, name: string): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value))
    return value as Record<string, unknown>
  invalid(`${name} must be an object`)
}

function isJson(value: unknown, depth = 0): value is JsonValue {
  if (depth > 32) return false
  if (value === null) return true
  switch (typeof value) {
    case "string":
    case "boolean":
      return true
    case "number":
      return Number.isFinite(value)
    case "object":
      if (Array.isArray(value))
        return value.every((item) => isJson(item, depth + 1))
      return (
        Object.getPrototypeOf(value) === Object.prototype &&
        Object.values(value).every((item) => isJson(item, depth + 1))
      )
    default:
      return false
  }
}

function jsonObject(value: unknown, name: string): JsonObject {
  if (
    !isJson(value) ||
    !value ||
    typeof value !== "object" ||
    Array.isArray(value)
  )
    invalid(`${name} must be a JSON object`)
  return value as JsonObject
}

/** `value`, the text of a JSON object; parsed, so keys Cap'n Web would drop survive. */
function jsonText(value: unknown, name: string, max: number): JsonObject {
  let parsed: unknown
  try {
    parsed = JSON.parse(text(value, name, max))
  } catch {
    invalid(`${name} must be JSON text`)
  }
  return jsonObject(parsed, name)
}

function toWire(stored: StoredDocument): StoredJson {
  return {
    key: stored.key,
    json: JSON.stringify(stored.document),
    updatedAt: stored.updatedAt,
  }
}

/** Commit files, sent as a list so no path is lost; at most 100. */
function fileList(value: unknown): Record<string, string> {
  if (!Array.isArray(value) || value.length > 100)
    invalid("files must be a list of at most 100")
  // fromEntries defines own properties, so a path like "__proto__" stays a path.
  return Object.fromEntries(
    value.map((entry) => {
      const file = record(entry, "file")
      if (typeof file.content !== "string")
        invalid("file content must be a string")
      return [text(file.path, "path", 1_000), file.content]
    })
  )
}

function stringList(value: unknown, name: string): Array<string> {
  const list = value ?? []
  if (!Array.isArray(list) || list.some((item) => typeof item !== "string"))
    invalid(`${name} must be a list of strings`)
  return list as Array<string>
}

/** A name the backend accepts as one path: no empty, `.` or `..` part. */
function pathName(
  value: unknown,
  name: string,
  pattern: RegExp,
  max = 200
): string {
  const checked = text(value, name, max)
  if (
    !pattern.test(checked) ||
    checked.split("/").some((part) => !part || part === "." || part === "..")
  )
    invalid(`${name} is not a valid name`)
  return checked
}

const COLLECTION = /^[a-z][a-z0-9_-]{0,63}$/
const DOCUMENT_KEY = /^[A-Za-z0-9._:@/-]{1,200}$/
const FIELD_PATH = /^[A-Za-z0-9_-]{1,64}(\.[A-Za-z0-9_-]{1,64}){0,7}$/

function cursor(value: unknown, name: string): CursorWire | undefined {
  if (value === undefined) return undefined
  const fields = record(value, name)
  const at: unknown = fields.value
  if (
    !(at === null || typeof at === "boolean" || typeof at === "string") &&
    !(typeof at === "number" && Number.isFinite(at))
  )
    invalid(`${name}.value must be a JSON string, number, boolean or null`)
  if (typeof at === "string" && at.length > 4_000)
    invalid(`${name}.value is too long`)
  if (typeof fields.inclusive !== "boolean")
    invalid(`${name}.inclusive must be a boolean`)
  return {
    value: at as JsonScalar,
    key:
      fields.key === undefined
        ? undefined
        : pathName(fields.key, `${name}.key`, DOCUMENT_KEY),
    inclusive: fields.inclusive,
  }
}
const ROOM = /^[A-Za-z0-9._:@/-]{1,200}$/
const BRANCH = /^[A-Za-z0-9._/-]{1,200}$/
const REPOSITORY = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/

const repositoryName = (value: unknown, name = "repository") =>
  pathName(value, name, REPOSITORY).toLowerCase()

function scopeOf(value: unknown): string | null {
  const scope = value === undefined ? {} : record(value, "scope")
  return scope.repository === undefined || scope.repository === null
    ? null
    : repositoryName(scope.repository)
}

function binary(value: unknown, name: string, max: number): Uint8Array {
  if (!(value instanceof Uint8Array) || value.byteLength > max)
    invalid(`${name} must be bytes, at most ${max}`)
  return value
}

function pluginPath(value: unknown): string {
  const checked = text(value, "path", 2_000)
  if (!checked.startsWith("/") || checked.startsWith("//"))
    invalid("path must start with a single /")
  return checked
}

function webUrl(value: unknown): URL {
  let url: URL
  try {
    url = new URL(text(value, "url", 4_000))
  } catch {
    invalid("url must be absolute")
  }
  if (url.protocol !== "https:" && url.protocol !== "http:")
    invalid("only web links can be opened")
  return url
}

/** A callback the plugin passed, kept past the call that brought it. */
function keep<F extends (...args: never[]) => unknown>(
  value: unknown,
  name: string
): RpcStub<F> {
  const stub = value as RpcStub<F> | undefined
  if (typeof stub !== "function" || typeof stub.dup !== "function")
    invalid(`${name} must be a function`)
  return stub.dup()
}

const TOOL_NAME = /^[a-z][a-z0-9_]{0,47}$/
const CHAT_NAME = /^[A-Za-z0-9._:@/-]{1,200}$/
const MAX_TOOLS = 32
/** The backend's `MAX_PLUGIN_CONTEXT`. */
const MAX_CONTEXT = 8_000
/** The most of a tool result the agent gets; the backend refuses more. */
export const MAX_TOOL_OUTPUT = 200_000

/** Text that may be empty or left out, at most `max` characters. */
function plainText(value: unknown, name: string, max: number): string {
  if (value === undefined) return ""
  if (typeof value !== "string" || value.length > max)
    invalid(`${name} must be text of at most ${max} characters`)
  return value
}

function toolOffer(json: unknown): AgentToolOffer {
  const fields = record(jsonText(json, "tools", 256_000), "tools")
  const tools = fields.tools
  if (!Array.isArray(tools) || tools.length > MAX_TOOLS)
    invalid(`tools must be a list of at most ${MAX_TOOLS}`)
  const checked = tools.map((tool: unknown) => {
    const entry = record(tool, "tool")
    const name = text(entry.name, "tool name", 48)
    if (!TOOL_NAME.test(name))
      invalid("a tool name is a lowercase letter, then letters, digits or _")
    const parameters = jsonObject(entry.parameters, `${name}.parameters`)
    if (parameters.type !== "object")
      invalid(`${name}.parameters must be a JSON Schema of type "object"`)
    return {
      name,
      description: plainText(entry.description, `${name}.description`, 2_000),
      parameters,
    }
  })
  if (new Set(checked.map((tool) => tool.name)).size < checked.length)
    invalid("tool names must be unique")
  return {
    tools: checked,
    instructions: plainText(fields.instructions, "instructions", 4_000),
  }
}

// ---- Limits -----------------------------------------------------------------------

export interface SessionLimits {
  /** Requests running at once; more wait their turn. */
  concurrency: number
  /** Requests running or waiting; more are refused. */
  pending: number
  /** Requests started in any minute; more are refused. */
  perMinute: number
  /** Capabilities (collections, repositories, rooms, subscriptions) held at once. */
  capabilities: number
  /** Events waiting on the plugin per listener; a listener this far behind is dropped. */
  backlog: number
}

export const SESSION_LIMITS: SessionLimits = {
  concurrency: 16,
  pending: 256,
  // A live room flushes edits every 50 ms; this leaves room for that and more.
  perMinute: 3_000,
  capabilities: 256,
  backlog: 512,
}

/** One frame's allowance, so a misbehaving plugin cannot flood the web UI or the backend. */
export class SessionBudget {
  #limits: SessionLimits
  #running = 0
  #waiting: Array<() => void> = []
  #started: Array<number> = []
  #capabilities = 0
  #closed = false

  constructor(limits: SessionLimits = SESSION_LIMITS) {
    this.#limits = limits
  }

  get limits(): SessionLimits {
    return this.#limits
  }

  run<T>(work: () => T | Promise<T>): Promise<T> {
    if (this.#closed)
      return Promise.reject(
        new BridgeRequestError("The plugin was closed", 410)
      )
    if (this.#running + this.#waiting.length >= this.#limits.pending)
      return Promise.reject(
        new BridgeRequestError("The plugin has too many pending requests", 429)
      )
    const now = Date.now()
    while (this.#started.length && this.#started[0]! < now - 60_000)
      this.#started.shift()
    if (this.#started.length >= this.#limits.perMinute)
      return Promise.reject(
        new BridgeRequestError("The plugin made too many requests", 429)
      )
    this.#started.push(now)
    return new Promise<T>((resolve, reject) => {
      const start = () => {
        this.#running += 1
        Promise.resolve()
          .then(work)
          .then(resolve, reject)
          .finally(() => {
            this.#running -= 1
            this.#waiting.shift()?.()
          })
      }
      if (this.#running < this.#limits.concurrency) start()
      else this.#waiting.push(start)
    })
  }

  /** Reserves one capability; the returned release frees it, once. */
  claim(): () => void {
    if (this.#capabilities >= this.#limits.capabilities)
      throw new BridgeRequestError(
        "The plugin holds too many capabilities",
        429
      )
    this.#capabilities += 1
    let held = true
    return () => {
      if (!held) return
      held = false
      this.#capabilities -= 1
    }
  }

  close(): void {
    this.#closed = true
  }
}

/**
 * Hands events to a plugin's callback in order. A callback that fails, or
 * falls `backlog` events behind, is dropped and `onDrop` runs.
 */
class Delivery<E> {
  #listener: RpcStub<(event: E) => void> | null
  #inFlight = 0
  #backlog: number
  #onDrop: () => void

  constructor(
    listener: RpcStub<(event: E) => void>,
    backlog: number,
    onDrop: () => void
  ) {
    this.#listener = listener
    this.#backlog = backlog
    this.#onDrop = onDrop
  }

  send(event: E): void {
    const listener = this.#listener
    if (!listener) return
    if (this.#inFlight >= this.#backlog) {
      this.#onDrop()
      return
    }
    this.#inFlight += 1
    Promise.resolve(listener(event as never)).then(
      () => {
        this.#inFlight -= 1
      },
      () => this.#onDrop()
    )
  }

  close(): void {
    this.#listener?.[Symbol.dispose]()
    this.#listener = null
  }
}

// ---- Capabilities -------------------------------------------------------------------

/** What a plugin disposes to stop a subscription or leave a room. */
class SubscriptionTarget extends RpcTarget implements Subscription {
  #stop: () => void

  constructor(stop: () => void) {
    super()
    this.#stop = stop
  }

  [Symbol.dispose](): void {
    this.#stop()
  }
}

/** Starts `listener`'s deliveries; the returned stop ends them and frees the capability. */
function subscription<E>(
  budget: SessionBudget,
  listener: unknown,
  name: string,
  start: (send: (event: E) => void) => () => void
): () => void {
  const kept = keep<(event: E) => void>(listener, name)
  let release: () => void
  try {
    release = budget.claim()
  } catch (error) {
    kept[Symbol.dispose]()
    throw error
  }
  let off = () => {}
  let stopped = false
  const stop = () => {
    if (stopped) return
    stopped = true
    off()
    delivery.close()
    release()
  }
  const delivery = new Delivery<E>(kept, budget.limits.backlog, stop)
  off = start((event) => delivery.send(event))
  return stop
}

/** The web UI, as a plugin's frame reaches it: the session's main capability. */
export class PluginHostApi extends RpcTarget implements HostApi {
  #host: () => BridgeHost
  #budget: SessionBudget

  constructor(host: () => BridgeHost, budget: SessionBudget) {
    super()
    this.#host = host
    this.#budget = budget
  }

  context(): Promise<HostContext> {
    return this.#budget.run(() => this.#host().context())
  }

  #require(permission: PluginPermission): void {
    if (!this.#host().plugin.manifest.permissions.includes(permission))
      throw new BridgeRequestError(
        `The plugin was not granted ${permission}`,
        403
      )
  }

  subscribe(listener: (event: HostEvent) => void): Promise<Subscription> {
    return this.#budget.run(
      () =>
        new SubscriptionTarget(
          subscription<HostEvent>(this.#budget, listener, "listener", (send) =>
            this.#host().subscribe(send)
          )
        )
    )
  }

  setPath(next: string, replace: boolean): Promise<void> {
    return this.#budget.run(() =>
      this.#host().navigate(pluginPath(next), replace === true)
    )
  }

  openThread(threadId: string): Promise<void> {
    return this.#budget.run(() =>
      this.#host().openThread(text(threadId, "threadId", 200))
    )
  }

  openExternal(link: string): Promise<void> {
    return this.#budget.run(async () => {
      const url = webUrl(link)
      if (
        url.hostname === "github.com" ||
        (await this.#host().confirmLink(url.toString()))
      )
        window.open(url.toString(), "_blank", "noopener,noreferrer")
    })
  }

  toast(toast: Toast): Promise<void> {
    return this.#budget.run(() => {
      const fields = record(toast, "toast")
      const intent = fields.intent
      this.#host().toast({
        title: text(fields.title, "title", 200),
        description: optionalText(fields.description, "description", 1_000),
        intent:
          intent === "success" || intent === "error" || intent === "info"
            ? intent
            : undefined,
      })
    })
  }

  setTitle(title: string | null): Promise<void> {
    return this.#budget.run(() =>
      this.#host().setTitle(optionalText(title, "title", 120) ?? null)
    )
  }

  people(logins: Array<string>) {
    return this.#budget.run(() => {
      const checked = stringList(logins, "logins")
      if (checked.length === 0 || checked.length > 100)
        invalid("logins must name 1 to 100 people")
      return pluginsApi.people(this.#host().plugin.id, checked)
    })
  }

  repositories(): Promise<Array<SharedRepository>> {
    return this.#budget.run(() =>
      this.#shared(this.#host().plugin.repositories)
    )
  }

  #shared(repositories: ReadonlyArray<string>): Array<SharedRepository> {
    const host = this.#host()
    return repositories.map((fullName) => ({
      fullName,
      isInBaseOrg: host.isInBaseOrg(fullName),
    }))
  }

  pickRepositories(
    options: RepositoryPickOptions
  ): Promise<Array<SharedRepository>> {
    return this.#budget.run(async () => {
      const fields = options === undefined ? {} : record(options, "options")
      const suggested = [
        ...new Set(
          stringList(fields.suggested, "suggested").map((name) =>
            repositoryName(name, "suggested")
          )
        ),
      ]
      if (suggested.length > 20) invalid("suggest at most 20 repositories")
      const host = this.#host()
      if (
        suggested.length > 0 &&
        suggested.every((name) => host.plugin.repositories.includes(name))
      )
        return this.#shared(suggested)
      return this.#shared(
        await host.pickRepositories({
          suggested,
          multiple: fields.multiple === true,
        })
      )
    })
  }

  collection(name: string, scope: DocumentScope): Promise<CollectionApi> {
    return this.#budget.run(
      () =>
        new CollectionTarget(
          this.#host,
          this.#budget,
          {
            collection: pathName(name, "collection", COLLECTION),
            repository: scopeOf(scope),
          },
          this.#budget.claim()
        )
    )
  }

  github(repository: string): Promise<GitHubRepositoryApi> {
    return this.#budget.run(
      () =>
        new RepositoryTarget(
          this.#host().plugin.id,
          this.#budget,
          repositoryName(repository),
          this.#budget.claim()
        )
    )
  }

  provideTools(
    json: string,
    run: (tool: string, argumentsJson: string) => Promise<string>
  ): Promise<Subscription> {
    return this.#budget.run(() => {
      this.#require("agent")
      const offer = toolOffer(json)
      const kept = keep<typeof run>(run, "run")
      let release: () => void
      try {
        release = this.#budget.claim()
      } catch (error) {
        kept[Symbol.dispose]()
        throw error
      }
      const withdraw = this.#host().provideTools(offer, async (tool, args) => {
        const output: unknown = await kept(tool, JSON.stringify(args))
        if (typeof output !== "string")
          throw new Error(`${tool} returned no text`)
        if (output.length <= MAX_TOOL_OUTPUT) return output
        const note = `\n[cut: the plugin returned ${output.length} characters]`
        return output.slice(0, MAX_TOOL_OUTPUT - note.length) + note
      })
      return new SubscriptionTarget(() => {
        withdraw()
        kept[Symbol.dispose]()
        release()
      })
    })
  }

  chooseChat(scope: ChatScope): Promise<void> {
    return this.#budget.run(() => {
      this.#require("agent")
      const fields = record(scope, "scope")
      const name = text(fields.name, "name", 200)
      if (!CHAT_NAME.test(name)) invalid("name is not a valid chat name")
      const host = this.#host()
      const repository = scopeOf(scope)
      if (repository !== null && !host.plugin.repositories.includes(repository))
        throw new BridgeRequestError(
          `${repository} is not shared with the plugin`,
          403
        )
      host.chooseChat({ name, repository })
    })
  }

  shareContext(json: string, label: string | null): Promise<void> {
    return this.#budget.run(() => {
      this.#require("agent")
      if (typeof json !== "string" || json.length > MAX_CONTEXT)
        invalid(`context must be JSON of at most ${MAX_CONTEXT} characters`)
      try {
        JSON.parse(json)
      } catch {
        invalid("context must be JSON")
      }
      this.#host().shareContext({
        json,
        label: optionalText(label, "label", 120) ?? null,
      })
    })
  }

  openChat(draft: string | null): Promise<void> {
    return this.#budget.run(() => {
      this.#require("agent")
      this.#host().openChat(optionalText(draft, "draft", 20_000) ?? null)
    })
  }

  joinRoom(
    scope: RoomScope,
    listener: (event: RoomEvent) => void,
    after: number
  ): Promise<RoomApi> {
    return this.#budget.run(() => {
      const fields = record(scope, "scope")
      const target: RoomTarget = {
        room: pathName(fields.room, "room", ROOM),
        repository: scopeOf(scope) ?? undefined,
      }
      const from = optionalInteger(after, "after") ?? 0
      if (from < 0) invalid("after must not be negative")
      const leave = subscription<RoomEvent>(
        this.#budget,
        listener,
        "listener",
        (send) => this.#host().joinRoom(target, from, send)
      )
      return new LiveRoomTarget(
        this.#host().plugin.id,
        this.#budget,
        target,
        leave
      )
    })
  }
}

/** One collection in one scope. */
class CollectionTarget extends RpcTarget implements CollectionApi {
  #host: () => BridgeHost
  #budget: SessionBudget
  #target: DocumentTarget
  #release: () => void
  #watches = new Set<() => void>()

  constructor(
    host: () => BridgeHost,
    budget: SessionBudget,
    target: DocumentTarget,
    release: () => void
  ) {
    super()
    this.#host = host
    this.#budget = budget
    this.#target = target
    this.#release = release
  }

  get(key: string): Promise<StoredJson | null> {
    return this.#budget.run(async () => {
      const found = await pluginsApi.getDocument(
        this.#host().plugin.id,
        this.#target.collection,
        pathName(key, "key", DOCUMENT_KEY),
        this.#target.repository ?? undefined
      )
      return found && toWire(found)
    })
  }

  query(query: QueryWire): Promise<Array<StoredJson>> {
    return this.#budget.run(async () => {
      const fields = query === undefined ? {} : record(query, "query")
      if (
        fields.direction !== undefined &&
        fields.direction !== "asc" &&
        fields.direction !== "desc"
      )
        invalid('direction must be "asc" or "desc"')
      const found = await pluginsApi.queryDocuments(
        this.#host().plugin.id,
        this.#target.collection,
        {
          repository: this.#target.repository ?? undefined,
          where:
            fields.where === undefined
              ? {}
              : jsonText(fields.where, "where", 64_000),
          order_by:
            fields.orderBy === undefined
              ? undefined
              : pathName(fields.orderBy, "orderBy", FIELD_PATH, 600),
          direction: fields.direction,
          prefix: optionalText(fields.prefix, "prefix", 200),
          start: cursor(fields.start, "start"),
          end: cursor(fields.end, "end"),
          limit: optionalInteger(fields.limit, "limit"),
        }
      )
      return found.map(toWire)
    })
  }

  put(key: string, json: string): Promise<StoredJson> {
    return this.#budget.run(async () =>
      toWire(
        await pluginsApi.putDocument(
          this.#host().plugin.id,
          this.#target.collection,
          pathName(key, "key", DOCUMENT_KEY),
          {
            repository: this.#target.repository ?? undefined,
            document: jsonText(json, "document", 512_000),
          }
        )
      )
    )
  }

  delete(key: string): Promise<void> {
    return this.#budget.run(async () => {
      await pluginsApi.deleteDocument(
        this.#host().plugin.id,
        this.#target.collection,
        pathName(key, "key", DOCUMENT_KEY),
        this.#target.repository ?? undefined
      )
    })
  }

  watch(onChange: () => void): Promise<Subscription> {
    return this.#budget.run(() => {
      const stop = subscription<undefined>(
        this.#budget,
        onChange,
        "onChange",
        (send) =>
          this.#host().watchDocuments(this.#target, () => send(undefined))
      )
      this.#watches.add(stop)
      return new SubscriptionTarget(() => {
        this.#watches.delete(stop)
        stop()
      })
    })
  }

  [Symbol.dispose](): void {
    for (const stop of this.#watches) stop()
    this.#watches.clear()
    this.#release()
  }
}

/** One repository the person shared, through their own GitHub access. */
class RepositoryTarget extends RpcTarget implements GitHubRepositoryApi {
  #plugin: string
  #budget: SessionBudget
  #repository: string
  #release: () => void

  constructor(
    plugin: string,
    budget: SessionBudget,
    repository: string,
    release: () => void
  ) {
    super()
    this.#plugin = plugin
    this.#budget = budget
    this.#repository = repository
    this.#release = release
  }

  info() {
    return this.#budget.run(() =>
      pluginsApi.repository(this.#plugin, this.#repository)
    )
  }

  ref(name: string) {
    return this.#budget.run(() =>
      pluginsApi.ref(this.#plugin, this.#repository, text(name, "name", 200))
    )
  }

  tree(sha: string) {
    return this.#budget.run(() =>
      pluginsApi.tree(this.#plugin, this.#repository, text(sha, "sha", 40))
    )
  }

  file(path: string, ref: string) {
    return this.#budget.run(() =>
      pluginsApi.file(
        this.#plugin,
        this.#repository,
        text(path, "path"),
        text(ref, "ref", 200)
      )
    )
  }

  image(path: string, ref: string): Promise<Blob | null> {
    return this.#budget.run(() =>
      pluginsApi.image(
        this.#plugin,
        this.#repository,
        text(path, "path"),
        text(ref, "ref", 200)
      )
    )
  }

  history(path: string, ref: string, limit?: number) {
    return this.#budget.run(() =>
      pluginsApi.history(
        this.#plugin,
        this.#repository,
        text(path, "path"),
        text(ref, "ref", 200),
        optionalInteger(limit, "limit")
      )
    )
  }

  createBranch(name: string, sha: string): Promise<void> {
    return this.#budget.run(async () => {
      await pluginsApi.createBranch(
        this.#plugin,
        this.#repository,
        text(name, "name", 200),
        text(sha, "sha", 40)
      )
    })
  }

  deleteBranch(name: string): Promise<void> {
    return this.#budget.run(() =>
      pluginsApi.deleteBranch(
        this.#plugin,
        this.#repository,
        pathName(name, "name", BRANCH)
      )
    )
  }

  commit(request: CommitWire): Promise<string> {
    return this.#budget.run(() => {
      const fields = record(request, "request")
      return pluginsApi.commit(this.#plugin, this.#repository, {
        branch: text(fields.branch, "branch", 200),
        expectedHead: text(fields.expectedHead, "expectedHead", 40),
        message: text(fields.message, "message", 10_000),
        files: fileList(fields.files),
        deletions: stringList(fields.deletions, "deletions"),
      })
    })
  }

  findPull(head: string) {
    return this.#budget.run(() =>
      pluginsApi.findPull(
        this.#plugin,
        this.#repository,
        text(head, "head", 200)
      )
    )
  }

  openPull(draft: PullRequestDraft) {
    return this.#budget.run(() => {
      const fields = record(draft, "draft")
      return pluginsApi.openPull(this.#plugin, this.#repository, {
        head: text(fields.head, "head", 200),
        base: text(fields.base, "base", 200),
        title: text(fields.title, "title", 256),
        body: optionalText(fields.body, "body", 60_000),
        draft: fields.draft === true,
      })
    })
  }

  [Symbol.dispose](): void {
    this.#release()
  }
}

/** One live room the plugin joined; disposing it leaves. */
class LiveRoomTarget extends RpcTarget implements RoomApi {
  #plugin: string
  #budget: SessionBudget
  #target: RoomTarget
  #leave: () => void

  constructor(
    plugin: string,
    budget: SessionBudget,
    target: RoomTarget,
    leave: () => void
  ) {
    super()
    this.#plugin = plugin
    this.#budget = budget
    this.#target = target
    this.#leave = leave
  }

  publish(update: Uint8Array): Promise<number> {
    return this.#budget.run(() =>
      pluginsApi.publishRoom(
        this.#plugin,
        this.#target,
        binary(update, "update", 1_000_000)
      )
    )
  }

  compact(snapshot: Uint8Array, through: number): Promise<number> {
    return this.#budget.run(() =>
      pluginsApi.compactRoom(
        this.#plugin,
        this.#target,
        binary(snapshot, "snapshot", 8_000_000),
        optionalInteger(through, "through") ?? 0
      )
    )
  }

  presence(client: string, presence: Uint8Array): Promise<void> {
    return this.#budget.run(async () => {
      await pluginsApi.announceInRoom(
        this.#plugin,
        this.#target,
        text(client, "client", 64),
        binary(presence, "presence", 2_900)
      )
    })
  }

  [Symbol.dispose](): void {
    this.#leave()
  }
}
