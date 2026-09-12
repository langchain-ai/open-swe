import { dashboardApiUrl } from "@/lib/dashboard-fetch"

export type VoiceStatus =
  | "idle"
  | "requesting"
  | "connecting"
  | "connected"
  | "stopping"
  | "error"

export interface VoiceSnapshot {
  status: VoiceStatus
  muted: boolean
  error: string | null
  transcript: Array<{ role: "You" | "Open SWE"; text: string }>
}

type JsonObject = Record<string, unknown>
type Navigate = (threadId: string) => void

const CONNECT_TIMEOUT = 20_000
const CLOSE_TIMEOUT = 5_000
const MAX_THREAD_ID = 200
const MAX_MESSAGE = 20_000
const MAX_QUERY = 500

function object(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null
}

async function request(path: string, init: RequestInit = {}): Promise<unknown> {
  const response = await fetch(dashboardApiUrl(path), {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init.headers },
  })
  if (!response.ok) {
    let message = response.statusText || `Request failed (${response.status})`
    try {
      const detail = object(await response.json())?.detail
      if (typeof detail === "string") message = detail
    } catch {}
    throw new Error(message)
  }
  return response.status === 204 ? null : response.json()
}

function requiredString(
  args: JsonObject,
  key: "thread_id" | "message" | "query",
  max: number
): string {
  const value = args[key]
  if (typeof value !== "string" || !value.trim() || value.length > max) {
    throw new Error(`Invalid ${key}`)
  }
  return value.trim()
}

function validateArgs(name: string, raw: unknown): JsonObject {
  let args: unknown
  try {
    args = typeof raw === "string" ? JSON.parse(raw) : raw
  } catch {
    throw new Error("Invalid tool arguments")
  }
  const parsed = object(args)
  if (!parsed) throw new Error("Invalid tool arguments")
  const allowed: Record<string, Array<string>> = {
    list_threads: ["query", "offset"],
    get_thread: ["thread_id"],
    create_thread: ["message"],
    send_message: ["thread_id", "message"],
    stop_thread: ["thread_id"],
    navigate_to_thread: ["thread_id"],
  }
  const keys = allowed[name]
  if (!keys || Object.keys(parsed).some((key) => !keys.includes(key))) {
    throw new Error("Invalid tool arguments")
  }
  if (keys.includes("thread_id"))
    requiredString(parsed, "thread_id", MAX_THREAD_ID)
  if (keys.includes("message")) requiredString(parsed, "message", MAX_MESSAGE)
  if (
    name === "list_threads" &&
    (typeof parsed.query !== "string" ||
      parsed.query.length > MAX_QUERY ||
      !Number.isInteger(parsed.offset) ||
      (parsed.offset as number) < 0)
  )
    throw new Error("Invalid list_threads arguments")
  return parsed
}

function messageText(value: unknown): string {
  if (typeof value === "string") return value
  if (!Array.isArray(value)) return ""
  return value
    .map((part) => {
      const item = object(part)
      return typeof item?.text === "string"
        ? item.text
        : typeof item?.content === "string"
          ? item.content
          : ""
    })
    .filter(Boolean)
    .join("\n")
}

function transcriptFromState(value: unknown): Array<JsonObject> {
  const values = object(object(value)?.values)
  const messages = values?.messages
  if (!Array.isArray(messages)) return []
  return messages.flatMap((raw) => {
    const message = object(raw)
    if (!message) return []
    const text = messageText(message.content)
    if (!text) return []
    return [{ role: message.type ?? message.role ?? "unknown", content: text }]
  })
}

export async function executeVoiceTool(
  name: string,
  rawArgs: unknown,
  navigate: Navigate
): Promise<JsonObject> {
  const args = validateArgs(name, rawArgs)
  const threadId = args.thread_id as string | undefined
  const message = args.message as string | undefined
  if (name === "list_threads") {
    const search = new URLSearchParams({
      limit: "25",
      offset: String(args.offset ?? 0),
      scope: "interactive",
      sort_by: "updated_at",
    })
    if (args.query) search.set("q", args.query as string)
    const page = object(await request(`/threads/page?${search}`))
    const items = Array.isArray(page?.items) ? page.items : []
    return {
      threads: items.flatMap((raw) => {
        const item = object(raw)
        return typeof item?.id === "string"
          ? [{ id: item.id, title: item.title, status: item.status }]
          : []
      }),
      has_more: page?.hasMore,
      offset: page?.offset,
    }
  }
  if (name === "get_thread") {
    const [summary, state] = await Promise.all([
      request(`/threads/${encodeURIComponent(threadId!)}?mark_viewed=false`),
      request(`/threads/${encodeURIComponent(threadId!)}/state`),
    ])
    return { thread: summary, transcript: transcriptFromState(state) }
  }
  if (name === "create_thread") {
    const id = crypto.randomUUID()
    await startRun(id, message!)
    return { status: "started", thread_id: id }
  }
  if (name === "send_message") {
    const thread = object(
      await request(
        `/threads/${encodeURIComponent(threadId!)}?mark_viewed=false`
      )
    )
    if (thread?.status === "running") {
      await request(`/threads/${encodeURIComponent(threadId!)}/messages`, {
        method: "POST",
        body: JSON.stringify({ content: message }),
      })
      return { status: "queued", thread_id: threadId }
    }
    await startRun(threadId!, message!)
    return { status: "started", thread_id: threadId }
  }
  if (name === "stop_thread") {
    await request(`/threads/${encodeURIComponent(threadId!)}/cancel`, {
      method: "POST",
    })
    return { status: "stopped", thread_id: threadId }
  }
  if (name === "navigate_to_thread") {
    await request(`/threads/${encodeURIComponent(threadId!)}?mark_viewed=false`)
    navigate(threadId!)
    return { status: "opened", thread_id: threadId }
  }
  throw new Error("Unknown tool")
}

function startRun(threadId: string, message: string): Promise<unknown> {
  return request(`/threads/${encodeURIComponent(threadId)}/commands`, {
    method: "POST",
    body: JSON.stringify({
      method: "run.start",
      params: { input: { messages: [{ role: "user", content: message }] } },
    }),
  })
}

function errorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === "NotAllowedError")
    return "Microphone permission was denied."
  return error instanceof Error ? error.message : "Voice session failed."
}

export class VoiceManager {
  private snapshot: VoiceSnapshot = {
    status: "idle",
    muted: false,
    error: null,
    transcript: [],
  }
  private listeners = new Set<() => void>()
  private peer: RTCPeerConnection | null = null
  private channel: RTCDataChannel | null = null
  private stream: MediaStream | null = null
  private audio: HTMLAudioElement | null = null
  private generation = 0
  private timer: ReturnType<typeof setTimeout> | null = null
  private calls = new Set<string>()
  private callQueue = Promise.resolve()
  private responseId: string | null = null
  private batches = new Map<
    string,
    Array<Promise<{ callId: string; output: JsonObject }>>
  >()

  constructor(private navigate: Navigate) {}

  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getSnapshot = () => this.snapshot

  private update(patch: Partial<VoiceSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch }
    this.listeners.forEach((listener) => listener())
  }

  async start() {
    if (this.snapshot.status !== "idle" && this.snapshot.status !== "error")
      return
    const generation = ++this.generation
    this.cleanup()
    this.calls.clear()
    this.responseId = null
    this.batches.clear()
    this.update({
      status: "requesting",
      error: null,
      transcript: [],
      muted: false,
    })
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (generation !== this.generation) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      this.stream = stream
      const peer = new RTCPeerConnection()
      this.peer = peer
      stream.getTracks().forEach((track) => peer.addTrack(track, stream))
      const audio = new Audio()
      audio.autoplay = true
      this.audio = audio
      peer.ontrack = ({ streams }) => {
        audio.srcObject = streams[0] ?? null
        void audio.play().catch(() => undefined)
      }
      peer.onconnectionstatechange = () => {
        if (["failed", "disconnected", "closed"].includes(peer.connectionState))
          this.fail("Voice connection was lost.", generation)
      }
      const channel = peer.createDataChannel("oai-events")
      this.channel = channel
      channel.onmessage = ({ data }) => this.onEvent(data, generation)
      channel.onerror = () =>
        this.fail("Voice event channel failed.", generation)
      const offer = await peer.createOffer()
      await peer.setLocalDescription(offer)
      this.update({ status: "connecting" })
      this.timer = setTimeout(
        () => this.fail("Voice connection timed out.", generation),
        CONNECT_TIMEOUT
      )
      const result = object(
        await request("/voice/session", {
          method: "POST",
          body: JSON.stringify({ sdp: peer.localDescription?.sdp }),
        })
      )
      const sdp = object(result?.transport)?.sdp
      if (typeof sdp !== "string")
        throw new Error("Invalid voice session response.")
      if (generation !== this.generation) return
      await peer.setRemoteDescription({ type: "answer", sdp })
    } catch (error) {
      this.fail(errorMessage(error), generation)
    }
  }

  stop() {
    if (this.snapshot.status === "idle") return
    const generation = ++this.generation
    this.invalidateCalls()
    this.stream?.getTracks().forEach((track) => track.stop())
    this.stream = null
    const channel = this.channel
    if (
      this.snapshot.status === "connected" &&
      channel?.readyState === "open"
    ) {
      this.update({ status: "stopping", muted: false })
      channel.onmessage = ({ data }) => this.onEvent(data, generation)
      channel.send(JSON.stringify({ type: "session.close" }))
      this.timer = setTimeout(() => this.finish(), CLOSE_TIMEOUT)
      return
    }
    this.finish()
  }

  toggleMute() {
    const muted = !this.snapshot.muted
    this.stream?.getAudioTracks().forEach((track) => (track.enabled = !muted))
    this.update({ muted })
  }

  destroy() {
    ++this.generation
    this.invalidateCalls()
    this.cleanup()
  }

  private onEvent(raw: unknown, generation: number) {
    if (generation !== this.generation) return
    let envelope: JsonObject | null = null
    try {
      envelope = object(JSON.parse(String(raw)))
    } catch {
      return
    }
    if (!envelope) return
    if (this.snapshot.status === "stopping") {
      if (envelope.type === "session.closed") this.finish()
      else if (envelope.type === "error")
        this.fail("The voice service reported an error.", generation)
      return
    }
    if (envelope.type === "session.started") {
      if (this.timer) clearTimeout(this.timer)
      this.timer = null
      this.update({ status: "connected" })
      return
    }
    if (envelope.type === "session.closed") {
      this.finish()
      return
    }
    if (envelope.type === "error") {
      this.fail("The voice service reported an error.", generation)
      return
    }
    if (
      envelope.type === "session.input_transcript.delta" ||
      envelope.type === "session.output_transcript.delta"
    ) {
      const delta = envelope.delta
      if (typeof delta === "string")
        this.appendTranscript(
          envelope.type === "session.input_transcript.delta"
            ? "You"
            : "Open SWE",
          delta
        )
      return
    }
    if (envelope.type !== "response.event") return
    const event = object(envelope.event)
    if (event?.type === "response.created") {
      const responseId = object(event.response)?.id
      if (typeof responseId === "string") this.responseId = responseId
      return
    }
    const eventResponseId = object(event?.response)?.id
    const responseId =
      typeof event?.response_id === "string"
        ? event.response_id
        : typeof eventResponseId === "string"
          ? eventResponseId
          : this.responseId
    if (event?.type === "response.output_item.done") {
      const item = object(event.item)
      const callId = item?.call_id
      const name = item?.name
      if (
        item?.type !== "function_call" ||
        typeof callId !== "string" ||
        typeof name !== "string" ||
        this.calls.has(callId)
      )
        return
      this.calls.add(callId)
      const result = this.callQueue.then(async () => {
        try {
          if (generation !== this.generation) throw new Error("Cancelled")
          return {
            callId,
            output: await executeVoiceTool(name, item.arguments, this.navigate),
          }
        } catch (error) {
          return {
            callId,
            output: { status: "error", error: errorMessage(error) },
          }
        }
      })
      this.callQueue = result.then(() => undefined)
      const key = responseId ?? callId
      this.batches.set(key, [...(this.batches.get(key) ?? []), result])
      return
    }
    if (event?.type !== "response.completed" || !responseId) return
    const pending = this.batches.get(responseId)
    if (!pending?.length) return
    this.batches.delete(responseId)
    void Promise.all(pending).then((results) => {
      if (generation !== this.generation || this.channel?.readyState !== "open")
        return
      for (const { callId, output } of results) {
        this.channel.send(
          JSON.stringify({
            type: "response.item.create",
            item: {
              type: "function_call_output",
              call_id: callId,
              output: JSON.stringify(output),
            },
          })
        )
      }
      this.channel.send(JSON.stringify({ type: "response.create" }))
    })
  }

  private appendTranscript(role: "You" | "Open SWE", delta: string) {
    const transcript = [...this.snapshot.transcript]
    const last = transcript.at(-1)
    if (last?.role === role) last.text += delta
    else transcript.push({ role, text: delta })
    this.update({ transcript })
  }

  private invalidateCalls() {
    this.calls.clear()
    this.responseId = null
    this.batches.clear()
    this.callQueue = Promise.resolve()
  }

  private fail(message: string, generation: number) {
    if (generation !== this.generation) return
    ++this.generation
    this.invalidateCalls()
    this.cleanup()
    this.update({ status: "error", error: message })
  }

  private finish() {
    ++this.generation
    this.invalidateCalls()
    this.cleanup()
    this.update({ status: "idle", muted: false })
  }

  private cleanup() {
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    this.channel?.close()
    this.peer?.close()
    this.stream?.getTracks().forEach((track) => track.stop())
    if (this.audio) {
      this.audio.pause()
      this.audio.srcObject = null
    }
    this.channel = null
    this.peer = null
    this.stream = null
    this.audio = null
  }
}
