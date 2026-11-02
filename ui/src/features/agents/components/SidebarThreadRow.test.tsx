/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import type { ComponentProps } from "react"

import { SidebarConversationRow } from "./SidebarThreadRow"

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, ...props }: ComponentProps<"a">) => (
    <a {...props}>{children}</a>
  ),
}))
vi.mock("@/features/agents/lib/queries", () => ({
  useRenameAgentThread: () => ({ isPending: false, mutate: vi.fn() }),
}))

afterEach(cleanup)

it("shows running before unread and retains unread until acknowledgement even on the active Home row", () => {
  const props = {
    title: "A Home conversation",
    threadId: "thread",
    status: "running" as const,
    viewed: false,
    active: true,
    compact: false,
    onNavigate: vi.fn(),
    pinned: false,
    archived: false,
    pending: false,
    onPin: vi.fn(),
    onArchive: vi.fn(),
  }
  const { rerender } = render(<SidebarConversationRow {...props} />, {
    wrapper: TooltipProvider,
  })
  expect(screen.getByRole("img", { name: "Chat running" })).toBeTruthy()
  expect(screen.queryByRole("img", { name: "Unread chat" })).toBeNull()

  rerender(<SidebarConversationRow {...props} status="finished" />)
  expect(screen.queryByRole("img", { name: "Chat running" })).toBeNull()
  expect(screen.getByRole("img", { name: "Unread chat" })).toBeTruthy()

  rerender(<SidebarConversationRow {...props} status="finished" viewed />)
  expect(screen.queryByRole("img", { name: "Unread chat" })).toBeNull()
})
