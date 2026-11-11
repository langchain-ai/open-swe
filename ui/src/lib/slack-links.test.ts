import { expect, it } from "vitest"

import { SlackLinks } from "./slack-links"

it.each([
  ["https://slack.com/archives/C123", "/dashboard/api/slack/open?channel=C123"],
  [
    "https://slack.com/app_redirect?channel=C123",
    "/dashboard/api/slack/open?channel=C123",
  ],
  [
    "slack://channel?id=D123&message=1700000000.000001",
    "/dashboard/api/slack/open?channel=D123&message_ts=1700000000.000001",
  ],
])("repairs historical ambiguous links: %s", (href, expected) => {
  expect(SlackLinks.normalize(href)).toBe(expected)
})

it.each([
  "https://example.slack.com/archives/C123/p1700000000000001",
  "https://slack.com/app_redirect?channel=C123&team=T123",
  "slack://channel?id=C123&team=T123",
  "https://github.com/langchain-ai/langchainplus/pull/1",
  "https://slack.com.evil.test/archives/C123/p1700000000000001",
])("preserves workspace-targeted or non-Slack links: %s", (href) => {
  expect(SlackLinks.normalize(href)).toBe(href)
})
