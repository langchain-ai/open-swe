/** @vitest-environment jsdom */

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import type { AgentThread } from "@/features/agents/lib/types"
import { ThreadMenuItems } from "./ThreadMenuItems"

const thread: AgentThread = {
  id: "cloud-thread-id",
  title: "Cloud thread",
  repo: "open-swe",
  repoFullName: "langchain-ai/open-swe",
  branch: "main",
  model: "",
  status: "idle",
  viewed: true,
  createdAt: 0,
  updatedAt: 0,
  messages: [],
}

const clipboardDescriptor = Object.getOwnPropertyDescriptor(
  navigator,
  "clipboard"
)

afterEach(() => {
  cleanup()
  if (clipboardDescriptor) {
    Object.defineProperty(navigator, "clipboard", clipboardDescriptor)
  } else {
    Reflect.deleteProperty(navigator, "clipboard")
  }
})

describe("Copy thread ID", () => {
  it.each([
    {
      name: "a cloud thread with a sandbox",
      props: { thread: { ...thread, sandboxId: "sandbox-id" } },
      expectedId: thread.id,
    },
    {
      name: "a cloud thread without a sandbox",
      props: { thread },
      expectedId: thread.id,
    },
  ])("copies the ID of $name", async ({ props, expectedId }) => {
    const writeText = vi
      .fn<(text: string) => Promise<void>>()
      .mockResolvedValue()
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    })

    render(
      <DropdownMenu>
        <DropdownMenuTrigger>Thread actions</DropdownMenuTrigger>
        <DropdownMenuContent>
          <ThreadMenuItems
            {...props}
            pinned={false}
            archived={false}
            isDeleting={false}
            onTogglePin={vi.fn()}
            onToggleViewed={vi.fn()}
            onToggleArchived={vi.fn()}
            onDelete={vi.fn()}
          />
        </DropdownMenuContent>
      </DropdownMenu>
    )

    fireEvent.keyDown(screen.getByRole("button", { name: "Thread actions" }), {
      key: "Enter",
    })
    fireEvent.click(
      await screen.findByRole("menuitem", { name: "Diagnostics" })
    )
    fireEvent.click(
      await screen.findByRole("menuitem", { name: "Copy thread ID" })
    )

    expect(writeText).toHaveBeenCalledWith(expectedId)
  })
})

describe("read state actions", () => {
  it.each([true, false])(
    "offers the opposite read state when viewed=%s",
    async (viewed) => {
      const toggle = vi.fn()
      render(
        <DropdownMenu>
          <DropdownMenuTrigger>Thread actions</DropdownMenuTrigger>
          <DropdownMenuContent>
            <ThreadMenuItems
              thread={{
                ...thread,
                viewed,
                traceUrl: "https://example.com/trace",
              }}
              pinned={false}
              archived={false}
              isDeleting={false}
              onRename={vi.fn()}
              onTogglePin={vi.fn()}
              onToggleViewed={toggle}
              onToggleArchived={vi.fn()}
              onDelete={vi.fn()}
            />
          </DropdownMenuContent>
        </DropdownMenu>
      )
      fireEvent.keyDown(
        screen.getByRole("button", { name: "Thread actions" }),
        { key: "Enter" }
      )
      expect(screen.queryByRole("menuitem", { name: "Open trace" })).toBeNull()
      expect(
        screen.queryByRole("menuitem", { name: "Copy thread ID" })
      ).toBeNull()
      fireEvent.click(
        await screen.findByRole("menuitem", {
          name: viewed ? "Mark unread" : "Mark read",
        })
      )
      expect(toggle).toHaveBeenCalledOnce()
    }
  )
})
