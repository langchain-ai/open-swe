// @vitest-environment jsdom

import { render } from "@testing-library/react"
import { StrictMode } from "react"
import { describe, expect, it } from "vitest"

import { PageModelContext, PageToolsProvider, usePageTool } from "./index"

function Counter({
  count,
  enabled = true,
}: {
  count: number
  enabled?: boolean
}) {
  usePageTool({
    name: "read_count",
    description: "The count on screen",
    inputSchema: {
      type: "object",
      properties: { plus: { type: "number" } },
      required: ["plus"],
    } as const,
    annotations: { readOnlyHint: true },
    enabled,
    execute: ({ plus }) => ({ count: count + plus }),
  })
  return null
}

function mount(context: PageModelContext, count: number, enabled?: boolean) {
  return (
    <StrictMode>
      <PageToolsProvider context={context}>
        <Counter count={count} enabled={enabled} />
      </PageToolsProvider>
    </StrictMode>
  )
}

describe("page tools", () => {
  it("offers a component's tool while it is mounted, reading its latest props", async () => {
    const context = new PageModelContext()
    const view = render(mount(context, 1))

    const [tool] = await context.getTools()
    expect(tool).toMatchObject({
      name: "read_count",
      description: "The count on screen",
      annotations: { readOnlyHint: true },
      inputSchema: { required: ["plus"] },
    })
    expect(await context.executeTool(tool!, { plus: 1 })).toBe('{"count":2}')

    view.rerender(mount(context, 5))
    expect(await context.getTools()).toHaveLength(1)
    expect(await context.executeTool(tool!, { plus: 1 })).toBe('{"count":6}')

    view.rerender(mount(context, 5, false))
    expect(await context.getTools()).toEqual([])
    await expect(context.executeTool(tool!, { plus: 1 })).rejects.toMatchObject(
      {
        name: "NotFoundError",
      }
    )

    view.rerender(mount(context, 5))
    view.unmount()
    expect(await context.getTools()).toEqual([])
  })

  it("follows WebMCP's registration and execution rules", async () => {
    const context = new PageModelContext()
    let changes = 0
    context.ontoolchange = () => {
      changes += 1
    }
    const registration = new AbortController()
    await context.registerTool(
      {
        name: "boom",
        description: "Fails",
        execute: () => {
          throw new Error("nope")
        },
      },
      { signal: registration.signal }
    )
    await expect(
      context.registerTool({
        name: "boom",
        description: "Again",
        execute: () => null,
      })
    ).rejects.toMatchObject({ name: "InvalidStateError" })
    await expect(
      context.registerTool({
        name: "has space",
        description: "Bad",
        execute: () => null,
      })
    ).rejects.toMatchObject({ name: "InvalidStateError" })

    const [tool] = await context.getTools()
    await expect(context.executeTool(tool!)).rejects.toMatchObject({
      name: "OperationError",
      message: "nope",
    })

    registration.abort()
    expect(await context.getTools()).toEqual([])
    expect(changes).toBe(2)
  })
})
