import { useEffect, useEffectEvent } from "react"
import type { WebMCP } from "webmcp-types"

import { usePageModelContext } from "./PageToolsProvider"

/** The schema of a tool that takes no input. */
type NoInput = { type: "object"; properties: Record<never, never> }

/** The input a tool's `execute` receives, inferred from its JSON Schema. */
export type PageToolInput<TSchema extends object> = Parameters<
  WebMCP.ModelContextToolFromSchema<TSchema>["execute"]
>[0]

/** A WebMCP tool as a component declares it. */
export interface PageTool<TSchema extends object> extends Omit<
  WebMCP.ModelContextTool,
  "inputSchema" | "execute"
> {
  /** JSON Schema of the input; write it `as const` so `execute` is typed from it. */
  inputSchema?: TSchema
  /**
   * Does the work and returns what the agent reads back, which must be
   * JSON. It always sees the component's latest props and state.
   */
  execute: (
    input: PageToolInput<TSchema>,
    options: WebMCP.ToolExecuteCallbackOptions
  ) => WebMCP.MaybePromise<unknown>
  /** Registered while true; defaults to true. */
  enabled?: boolean
}

/**
 * Offers Robin's chat a tool for as long as the calling component is mounted.
 *
 * The tool acts on what the component owns: call its store actions or state
 * setters from `execute` rather than reaching into the DOM, so the page and
 * the agent go through the same code paths. Changing the name, description,
 * schema or annotations re-registers the tool; changing `execute` does not.
 */
export function usePageTool<const TSchema extends object = NoInput>(
  tool: PageTool<TSchema>
): void {
  const context = usePageModelContext()
  const execute = useEffectEvent(
    (
      input: PageToolInput<TSchema>,
      options: WebMCP.ToolExecuteCallbackOptions
    ) => tool.execute(input, options)
  )
  const { name, title, description, inputSchema, annotations } = tool
  const enabled = tool.enabled ?? true
  const descriptor = JSON.stringify({
    name,
    title,
    description,
    inputSchema,
    annotations,
  })

  useEffect(() => {
    if (!enabled) return
    const registration = new AbortController()
    const described = JSON.parse(descriptor) as Omit<
      WebMCP.ModelContextTool,
      "execute"
    >
    context
      .registerTool(
        {
          ...described,
          execute: (input, options) =>
            execute(input as PageToolInput<TSchema>, options),
        },
        { signal: registration.signal }
      )
      .catch((error: unknown) => {
        if (!registration.signal.aborted)
          console.error(`Could not register page tool "${name}"`, error)
      })
    return () => registration.abort()
  }, [context, descriptor, enabled, name])
}
