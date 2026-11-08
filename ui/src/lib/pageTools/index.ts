/**
 * Page tools: what Robin's chat can do to the page the person has open.
 *
 * Components declare tools with `usePageTool`, written to the WebMCP tool
 * contract. They register on a private `PageModelContext`, never on
 * `document.modelContext`, so only Robin's own chat can see or call them.
 */
export { PageModelContext } from "./PageModelContext"
export { PageToolsProvider, usePageModelContext } from "./PageToolsProvider"
export { usePageTool } from "./usePageTool"
export type { PageTool, PageToolInput } from "./usePageTool"
