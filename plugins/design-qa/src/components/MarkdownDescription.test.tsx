import type { PluginHost } from "@openswe/plugin-sdk"
import { PluginHostProvider } from "@openswe/plugin-sdk/react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

import { MarkdownDescription } from "./MarkdownDescription"

describe("MarkdownDescription", () => {
  it("keeps footnote references and backlinks inside their own description", () => {
    const body =
      "See the notes.[^verify] Again.[^verify]\n\n[^verify]: Checked on mobile."
    const html = renderToStaticMarkup(
      <PluginHostProvider host={{} as PluginHost}>
        <MarkdownDescription body={body} />
        <MarkdownDescription body={body} />
      </PluginHostProvider>
    )
    const ids = [...html.matchAll(/ id="([^"]+)"/g)].map((match) => match[1])
    const destinations = [...html.matchAll(/ href="#([^"]+)"/g)].map(
      (match) => match[1]
    )
    expect(destinations).toHaveLength(8)
    expect(new Set(ids).size).toBe(ids.length)
    for (const destination of destinations) expect(ids).toContain(destination)
    for (const label of html.matchAll(/ aria-describedby="([^"]+)"/g))
      expect(ids).toContain(label[1])
  })

  it("keeps table cells and GitHub destinations without rendering unsafe links or fetching images", () => {
    const body = [
      "| Before | After |",
      "| --- | --- |",
      "| Menu hidden | Menu visible |",
      "",
      "[Tracking issue](/acme/web/issues/7)",
      "[External](https://example.com/guide)",
      "[Unsafe](javascript:alert%281%29)",
      "[Network path](//example.com/guide)",
      "[Backslash](/%5cexample.com/guide)",
      "![Private image](https://example.com/private.png)",
      "<img src='https://example.com/raw.png' onerror='alert(1)' />",
      "<script>alert(1)</script>",
    ].join("\n")
    const html = renderToStaticMarkup(
      <PluginHostProvider host={{} as PluginHost}>
        <MarkdownDescription body={body} />
      </PluginHostProvider>
    )

    expect(html).toContain("<th>Before</th>")
    expect(html).toContain("<td>Menu visible</td>")
    expect(html).toContain('href="https://github.com/acme/web/issues/7"')
    expect(html).toContain('href="https://example.com/guide"')
    expect(html).not.toMatch(/href="(?:javascript:|\/\/)/)
    expect(html).not.toMatch(/<(?:img|script)\b/)
    expect(html).not.toContain("private.png")
    expect(html).not.toContain("raw.png")
    expect(html).toContain("[Image: Private image]")
  })
})
