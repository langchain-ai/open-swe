import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

import { SlackMrkdwn } from "./SlackMrkdwn"

describe("SlackMrkdwn", () => {
  it("renders Slack links, mentions, and inline formatting", () => {
    const html = renderToStaticMarkup(
      <SlackMrkdwn text="Read *important* <https://example.com/docs|docs> with <@U123|Alice>" />
    )

    expect(html).toContain("<strong>important</strong>")
    expect(html).toContain('href="https://example.com/docs"')
    expect(html).toContain(">docs</a>")
    expect(html).toContain("@Alice")
  })

  it("keeps formatting active across Slack tokens", () => {
    const html = renderToStaticMarkup(
      <SlackMrkdwn
        text={
          "*See <https://example.com/docs|docs>* and <https://example.com/encoded|R&amp;amp;D>"
        }
      />
    )

    expect(html).toContain("<strong>See <a")
    expect(html).toContain(">docs</a></strong>")
    expect(html).toContain(">R&amp;amp;D</a>")

    const underscoredUrl = renderToStaticMarkup(
      <SlackMrkdwn text="_See <https://example.com/foo_bar|docs>_" />
    )
    expect(underscoredUrl).toContain("<em>See <a")
    expect(underscoredUrl).toContain(">docs</a></em>")
  })

  it("keeps code spans inert and decodes Slack entities", () => {
    const html = renderToStaticMarkup(
      <SlackMrkdwn
        text={
          "R&amp;D `<https://example.com/code|code docs>` <!date^1700000000^{date_short}|Posted Nov 14>"
        }
      />
    )

    expect(html).toContain("R&amp;D")
    expect(html).toContain("&lt;https://example.com/code|code docs&gt;")
    expect(html).not.toContain('href="https://example.com/code"')
    expect(html).toContain("Posted Nov 14")
    expect(html).not.toContain("@Posted Nov 14")
  })

  it("does not create links for unsupported URL schemes", () => {
    const html = renderToStaticMarkup(
      <SlackMrkdwn text="<javascript:alert(1)|unsafe>" />
    )

    expect(html).not.toContain("href=")
    expect(html).toContain("unsafe")
  })
})
