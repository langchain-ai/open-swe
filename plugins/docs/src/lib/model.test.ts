import { expect, it } from "vitest"
import { presenceRoom, resolveLink, roomName } from "@/lib/model"

it("keeps long Unicode page paths within the host room-name limits", async () => {
  const identity = presenceRoom(
    `${"長い directory/".repeat(30)}Getting Started (+).md`
  )
  const name = await roomName(identity)
  expect(name).toMatch(/^[A-Za-z0-9._:@/-]{1,200}$/)
  expect(await roomName(identity)).toBe(name)
  expect(await roomName(`${identity}a`)).not.toBe(name)
})

it("ignores a link with a malformed escape instead of throwing", () => {
  expect(resolveLink("100%.md", "docs/page.md")).toBeNull()
})
