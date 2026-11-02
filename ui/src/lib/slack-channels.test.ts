/** @vitest-environment jsdom */

import { beforeEach, expect, it } from "vitest"

import type { SlackChannelOption } from "@/lib/api"
import { rankSlackChannels, rememberSlackChannels } from "@/lib/slack-channels"

const channel = (
  id: string,
  name: string,
  is_member = true
): SlackChannelOption => ({
  id,
  name,
  is_private: false,
  is_member,
  is_ext_shared: false,
  num_members: null,
})

const directory = [
  channel("C00000000A1", "alerts"),
  channel("C00000000B2", "builds", false),
  channel("C00000000C3", "community"),
  channel("C00000000D4", "deploys"),
]

beforeEach(() => window.localStorage.clear())

it("offers recently chosen channels first, newest first, then members", () => {
  rememberSlackChannels(["C00000000B2"])
  rememberSlackChannels(["C00000000D4", "C00000000B2"])

  expect(
    rankSlackChannels(directory, "", readRecent()).map(({ name }) => name)
  ).toEqual(["deploys", "builds", "alerts", "community"])
  expect(
    rankSlackChannels(directory, "#d", readRecent()).map(({ name }) => name)
  ).toEqual(["deploys", "builds"])
})

it("keeps only valid channel ids from storage", () => {
  window.localStorage.setItem(
    "open-swe.slack-channels.recent.v1",
    JSON.stringify(["C00000000C3", "not-an-id", 7])
  )
  rememberSlackChannels(["C00000000A1"])

  expect(readRecent()).toEqual(["C00000000A1", "C00000000C3"])
})

function readRecent(): Array<string> {
  return JSON.parse(
    window.localStorage.getItem("open-swe.slack-channels.recent.v1") ?? "[]"
  )
}
