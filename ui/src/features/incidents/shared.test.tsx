/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, expect, it } from "vitest"
import { CitedText, sourceLabel, slackMessageTime } from "./shared"

afterEach(cleanup)

it("labels unavailable historical citations without exposing source IDs", () => {
  render(
    <CitedText
      text="Earlier finding [slack:old]. Reported at [12:30] [unverified]."
      evidence={[]}
    />
  )
  expect(screen.queryByText(/slack:old/)).toBeNull()
  expect(screen.getByText("[source unavailable]")).toBeTruthy()
  expect(screen.getByText(/\[12:30\] \[unverified\]/)).toBeTruthy()
})

it("renders grouped evidence as individual source links and preserves ordinary brackets", () => {
  const evidence = ["slack:1", "slack:2"].map((id, index) => ({
    id,
    source: "slack",
    summary: "Message in the incident channel",
    url: `https://slack.com/archives/C1/p${index + 1}`,
    retrieved_at: 1789256870,
  }))
  render(
    <CitedText
      text="Recovered [slack:1, slack:2]. [unverified]"
      evidence={evidence}
    />
  )
  expect(
    screen.getAllByRole("link").map((link) => link.getAttribute("href"))
  ).toEqual(evidence.map((item) => item.url))
  expect(screen.queryByText(/slack:1/)).toBeNull()
  expect(screen.getByText(/\[unverified\]/)).toBeTruthy()
})

it("presents stored Slack labels without raw author IDs or Unix timestamps", () => {
  expect(
    sourceLabel(
      "Slack message at 1789256856.755619; author U0C0NQ5LNL8.",
      "inc-api"
    )
  ).toBe("Message in #inc-api")
  expect(
    slackMessageTime("https://slack.com/archives/C1/p1789256856755619")
  ).toBe(1789256856)
  expect(sourceLabel("Database latency unchanged", "inc-api")).toBe(
    "Database latency unchanged"
  )
})
