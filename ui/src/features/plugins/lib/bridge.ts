/**
 * The dashboard's side of the plugin protocol: what each request from a
 * plugin's frame does. Plugins are untrusted, so every request is checked for
 * shape here and for permission again by the backend, which is the authority.
 */
import type {
  HostMethod,
  HostMethods,
  JsonObject,
  JsonValue,
  ResultOf,
} from "@openswe/plugin-sdk"

import type { InstalledPlugin, RoomTarget } from "@/features/plugins/lib/api"
import { pluginsApi } from "@/features/plugins/lib/api"

export class BridgeRequestError extends Error {
  constructor(
    message: string,
    readonly status: number
  ) {
    super(message)
    this.name = "BridgeRequestError"
  }
}

export interface ThreadRequest {
  repository: string
  title: string
  request: string
}

/** What a request may do in the dashboard beyond calling the backend. */
export interface BridgeHost {
  plugin: InstalledPlugin
  navigate: (path: string, replace: boolean) => void
  openThread: (threadId: string) => void
  toast: (toast: HostMethods["ui.toast"]["params"]) => void
  /** Shows the person the request word for word; resolves whether they approved it. */
  confirmThread: (request: ThreadRequest) => Promise<boolean>
  /** Asks before leaving for a site other than GitHub; resolves whether to go. */
  confirmLink: (url: string) => Promise<boolean>
  watch: (repository: string | null) => void
  unwatch: (repository: string | null) => void
  joinRoom: (target: RoomTarget, after: number) => void
  leaveRoom: (target: RoomTarget) => void
}

type Params = Record<string, unknown>

function record(value: unknown): Params {
  if (value && typeof value === "object" && !Array.isArray(value))
    return value as Params
  throw new BridgeRequestError("params must be an object", 400)
}

function text(params: Params, key: string, max = 1_000): string {
  const value = params[key]
  if (typeof value !== "string" || !value || value.length > max)
    throw new BridgeRequestError(`${key} must be a non-empty string`, 400)
  return value
}

function optionalText(params: Params, key: string, max = 1_000) {
  return params[key] === undefined || params[key] === null
    ? undefined
    : text(params, key, max)
}

function optionalNumber(params: Params, key: string): number | undefined {
  const value = params[key]
  if (value === undefined) return undefined
  if (typeof value !== "number" || !Number.isInteger(value))
    throw new BridgeRequestError(`${key} must be an integer`, 400)
  return value
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
      return Array.isArray(value)
        ? value.every((item) => isJson(item, depth + 1))
        : Object.values(value).every((item) => isJson(item, depth + 1))
    default:
      return false
  }
}

function jsonObject(params: Params, key: string): JsonObject {
  const value = params[key] ?? {}
  if (!isJson(value) || typeof value !== "object" || Array.isArray(value))
    throw new BridgeRequestError(`${key} must be a JSON object`, 400)
  return value as JsonObject
}

function stringMap(params: Params, key: string): Record<string, string> {
  const value = record(params[key])
  for (const item of Object.values(value))
    if (typeof item !== "string")
      throw new BridgeRequestError(`${key} values must be strings`, 400)
  return value as Record<string, string>
}

function stringList(params: Params, key: string): Array<string> {
  const value = params[key] ?? []
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string"))
    throw new BridgeRequestError(`${key} must be a list of strings`, 400)
  return value as Array<string>
}

/** A name the backend accepts as one path: no empty, `.` or `..` part. */
function pathName(
  params: Params,
  key: string,
  pattern: RegExp,
  max = 200
): string {
  const value = text(params, key, max)
  if (
    !pattern.test(value) ||
    value.split("/").some((part) => !part || part === "." || part === "..")
  )
    throw new BridgeRequestError(`${key} is not a valid name`, 400)
  return value
}

const COLLECTION = /^[a-z][a-z0-9_-]{0,63}$/
const DOCUMENT_KEY = /^[A-Za-z0-9._:@/-]{1,200}$/
const ROOM = /^[A-Za-z0-9._:@/-]{1,200}$/
const REPOSITORY = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/

const collection = (params: Params) =>
  pathName(params, "collection", COLLECTION)
const documentKey = (params: Params) => pathName(params, "key", DOCUMENT_KEY)
const repository = (params: Params) =>
  pathName(params, "repository", REPOSITORY)
const optionalRepository = (params: Params) =>
  params.repository === undefined || params.repository === null
    ? undefined
    : repository(params)

function binary(params: Params, key: string, max: number): Uint8Array {
  const value = params[key]
  if (!(value instanceof Uint8Array) || value.byteLength > max)
    throw new BridgeRequestError(`${key} must be bytes, at most ${max}`, 400)
  return value
}

function roomTarget(params: Params): RoomTarget {
  return {
    room: pathName(params, "room", ROOM),
    repository: optionalRepository(params)?.toLowerCase(),
  }
}

function path(params: Params): string {
  const value = text(params, "path", 2_000)
  if (!value.startsWith("/") || value.startsWith("//"))
    throw new BridgeRequestError("path must start with a single /", 400)
  return value
}

function webUrl(params: Params): URL {
  let url: URL
  try {
    url = new URL(text(params, "url", 4_000))
  } catch {
    throw new BridgeRequestError("url must be absolute", 400)
  }
  if (url.protocol !== "https:" && url.protocol !== "http:")
    throw new BridgeRequestError("only web links can be opened", 400)
  return url
}

type Handlers = {
  [M in HostMethod]: (host: BridgeHost, params: Params) => Promise<ResultOf<M>>
}

const handlers: Handlers = {
  "nav.setPath": async (host, params) => {
    host.navigate(path(params), params.replace === true)
    return null
  },
  "nav.openThread": async (host, params) => {
    host.openThread(text(params, "threadId", 200))
    return null
  },
  "nav.openExternal": async (host, params) => {
    const url = webUrl(params)
    if (
      url.hostname === "github.com" ||
      (await host.confirmLink(url.toString()))
    )
      window.open(url.toString(), "_blank", "noopener,noreferrer")
    return null
  },
  "people.get": (host, params) => {
    const logins = stringList(params, "logins")
    if (logins.length === 0 || logins.length > 100)
      throw new BridgeRequestError("logins must name 1 to 100 people", 400)
    return pluginsApi.people(host.plugin.id, logins)
  },
  "ui.toast": async (host, params) => {
    const intent = params.intent
    host.toast({
      title: text(params, "title", 200),
      description: optionalText(params, "description", 1_000),
      intent:
        intent === "success" || intent === "error" || intent === "info"
          ? intent
          : undefined,
    })
    return null
  },

  "storage.query": (host, params) =>
    pluginsApi.queryDocuments(host.plugin.id, collection(params), {
      repository: optionalRepository(params),
      where: jsonObject(params, "where"),
      limit: optionalNumber(params, "limit"),
    }),
  "storage.get": (host, params) =>
    pluginsApi.getDocument(
      host.plugin.id,
      collection(params),
      documentKey(params),
      optionalRepository(params)
    ),
  "storage.put": (host, params) => {
    if (!isJson(params.document))
      throw new BridgeRequestError("document must be JSON", 400)
    return pluginsApi.putDocument(
      host.plugin.id,
      collection(params),
      documentKey(params),
      {
        repository: optionalRepository(params),
        document: params.document,
      }
    )
  },
  "storage.delete": async (host, params) => {
    await pluginsApi.deleteDocument(
      host.plugin.id,
      collection(params),
      documentKey(params),
      optionalRepository(params)
    )
    return null
  },
  "storage.watch": async (host, params) => {
    host.watch(optionalRepository(params)?.toLowerCase() ?? null)
    return null
  },
  "storage.unwatch": async (host, params) => {
    host.unwatch(optionalRepository(params)?.toLowerCase() ?? null)
    return null
  },

  "github.repository": (host, params) =>
    pluginsApi.repository(host.plugin.id, repository(params)),
  "github.ref": (host, params) =>
    pluginsApi.ref(
      host.plugin.id,
      repository(params),
      text(params, "name", 200)
    ),
  "github.tree": (host, params) =>
    pluginsApi.tree(
      host.plugin.id,
      repository(params),
      text(params, "sha", 40)
    ),
  "github.file": (host, params) =>
    pluginsApi.file(
      host.plugin.id,
      repository(params),
      text(params, "path"),
      text(params, "ref", 200)
    ),
  "github.history": (host, params) =>
    pluginsApi.history(
      host.plugin.id,
      repository(params),
      text(params, "path"),
      text(params, "ref", 200),
      optionalNumber(params, "limit")
    ),
  "github.createBranch": async (host, params) => {
    await pluginsApi.createBranch(
      host.plugin.id,
      repository(params),
      text(params, "name", 200),
      text(params, "sha", 40)
    )
    return null
  },
  "github.commit": (host, params) =>
    pluginsApi.commit(host.plugin.id, repository(params), {
      branch: text(params, "branch", 200),
      expectedHead: text(params, "expectedHead", 40),
      message: text(params, "message", 10_000),
      files: stringMap(params, "files"),
      deletions: stringList(params, "deletions"),
    }),
  "github.findPull": (host, params) =>
    pluginsApi.findPull(
      host.plugin.id,
      repository(params),
      text(params, "head", 200)
    ),
  "github.openPull": (host, params) =>
    pluginsApi.openPull(host.plugin.id, repository(params), {
      head: text(params, "head", 200),
      base: text(params, "base", 200),
      title: text(params, "title", 256),
      body: optionalText(params, "body", 60_000),
      draft: params.draft === true,
    }),

  "threads.start": async (host, params) => {
    const request: ThreadRequest = {
      repository: repository(params),
      title: text(params, "title", 120),
      request: text(params, "request", 40_000),
    }
    if (!(await host.confirmThread(request))) return null
    const started = await pluginsApi.startThread(host.plugin.id, request)
    return { threadId: started.thread_id }
  },
  "threads.list": (host) => pluginsApi.threads(host.plugin.id),

  "rooms.join": async (host, params) => {
    host.joinRoom(roomTarget(params), optionalNumber(params, "after") ?? 0)
    return null
  },
  "rooms.leave": async (host, params) => {
    host.leaveRoom(roomTarget(params))
    return null
  },
  "rooms.publish": (host, params) =>
    pluginsApi.publishRoom(
      host.plugin.id,
      roomTarget(params),
      binary(params, "update", 1_000_000)
    ),
  "rooms.compact": (host, params) =>
    pluginsApi.compactRoom(
      host.plugin.id,
      roomTarget(params),
      binary(params, "snapshot", 8_000_000),
      optionalNumber(params, "through") ?? 0
    ),
  "rooms.presence": async (host, params) => {
    await pluginsApi.announceInRoom(
      host.plugin.id,
      roomTarget(params),
      text(params, "client", 64),
      binary(params, "presence", 2_900)
    )
    return null
  },
}

/** Always settles: a request that fails validation still gets an answer. */
export async function handleBridgeRequest(
  host: BridgeHost,
  method: unknown,
  params: unknown
): Promise<unknown> {
  if (typeof method !== "string" || !Object.hasOwn(handlers, method))
    throw new BridgeRequestError("unknown method", 404)
  return handlers[method as HostMethod](host, record(params))
}
