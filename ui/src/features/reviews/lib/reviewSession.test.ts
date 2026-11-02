/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it } from "vitest"

import {
  useReviewSessions,
  type AgentComment,
  type ReviewSession,
} from "./reviewSession"

const KEY = "acme/app#7"

function comment(id: string, body = `about ${id}`): AgentComment {
  return {
    id,
    path: "src/app.ts",
    range: { start: 4, end: 6, side: "additions" },
    excerpts: [
      {
        path: "src/app.ts",
        lineLabel: "R4-6",
        language: "ts",
        snippet: "> 4 | const a = 1",
      },
    ],
    headSha: "a".repeat(40),
    body,
    createdAt: 0,
  }
}

const store = () => useReviewSessions.getState()
const session = (): ReviewSession | undefined => store().sessions[KEY]

/** Starts a session, drafts two comments and hands them to the chat. */
function handOff(): string {
  store().add(KEY, comment("one"))
  store().add(KEY, comment("two"))
  store().send(KEY, "queue", "a".repeat(40))
  const id = session()?.handoff?.messageId
  if (!id) throw new Error("no handoff")
  return id
}

describe("review session handoff", () => {
  beforeEach(() => useReviewSessions.setState({ sessions: {} }))

  it("sends every draft as one message without touching the chat until finished", () => {
    store().start(KEY)
    store().add(KEY, comment("one"))
    expect(session()).toMatchObject({ active: true, handoff: null })

    const id = handOff()
    const handoff = session()?.handoff
    expect(session()?.active).toBe(false)
    expect(session()?.comments).toEqual([])
    expect(handoff?.status).toBe("sending")
    expect(handoff?.text).toContain("src/app.ts:R4-6")
    expect(handoff?.text).toContain("about two")
    expect(id).toBeTruthy()
  })

  it("follows a scheduled batch to the agent and then lets it go", () => {
    const id = handOff()
    store().dispatched(KEY, id)
    expect(store().observe(KEY, id, { kind: "queued" })).toBeNull()
    expect(session()?.handoff?.status).toBe("scheduled")
    store().observe(KEY, id, { kind: "delivered", running: true })
    expect(session()?.handoff?.status).toBe("delivered")
    expect(
      store().observe(KEY, id, { kind: "delivered", running: false })
    ).toBe("finished")
    expect(session()).toBeUndefined()
  })

  it("returns a withdrawn batch to drafts ahead of comments written since", () => {
    const id = handOff()
    store().dispatched(KEY, id)
    store().observe(KEY, id, { kind: "queued" })
    store().add(KEY, comment("three"))
    expect(store().observe(KEY, id, { kind: "missing", settled: true })).toBe(
      "returned"
    )
    expect(session()?.handoff).toBeNull()
    expect(session()?.active).toBe(true)
    expect(session()?.comments.map((item) => item.id)).toEqual([
      "one",
      "two",
      "three",
    ])
  })

  it("keeps the drafts when sending fails", () => {
    const id = handOff()
    store().dispatched(KEY, id)
    expect(
      store().observe(KEY, id, { kind: "failed", error: "500 down" })
    ).toBe("failed")
    expect(session()).toMatchObject({ active: true, error: "500 down" })
    expect(session()?.comments).toHaveLength(2)
  })

  it("waits for a batch that is still on its way instead of calling it lost", () => {
    const id = handOff()
    // Not dispatched yet: nothing the chat shows can move it.
    expect(
      store().observe(KEY, id, { kind: "missing", settled: true })
    ).toBeNull()
    store().dispatched(KEY, id)
    expect(
      store().observe(KEY, id, { kind: "missing", settled: false })
    ).toBeNull()
    expect(session()?.handoff?.status).toBe("sent")
    expect(store().observe(KEY, id, { kind: "missing", settled: true })).toBe(
      "lost"
    )
    expect(session()?.comments).toHaveLength(2)
  })

  it("drops anything unrecognized when reading drafts back from storage", async () => {
    localStorage.setItem(
      "open-swe:review-sessions",
      JSON.stringify({
        state: {
          sessions: {
            [KEY]: {
              active: true,
              comments: [comment("kept"), { id: "broken" }],
              handoff: { messageId: "m", status: "bogus" },
            },
          },
        },
        version: 1,
      })
    )
    await useReviewSessions.persist.rehydrate()
    expect(session()?.comments.map((item) => item.id)).toEqual(["kept"])
    expect(session()?.handoff).toBeNull()
  })
})
