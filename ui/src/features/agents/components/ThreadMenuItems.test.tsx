/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { Menu, MenuPopup, MenuTrigger } from "@/components/ui/menu"
import type { DesktopLocalThreadSummary } from "@/desktop"
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

const localThread: DesktopLocalThreadSummary = {
  id: "local-thread-id",
  title: "Local thread",
  cwd: "/workspace/open-swe",
  worktreePath: null,
  viewed: true,
  createdAt: 0,
  updatedAt: 0,
  modelId: null,
  effort: null,
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
    {
      name: "a local thread",
      props: { thread: null, localThread },
      expectedId: localThread.id,
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
      <Menu>
        <MenuTrigger>Thread actions</MenuTrigger>
        <MenuPopup>
          <ThreadMenuItems
            {...props}
            pinned={false}
            archived={false}
            isDeleting={false}
            onTogglePin={vi.fn()}
            onToggleArchived={vi.fn()}
            onDelete={vi.fn()}
          />
        </MenuPopup>
      </Menu>
    )

    fireEvent.click(screen.getByRole("button", { name: "Thread actions" }))
    fireEvent.click(
      await screen.findByRole("menuitem", { name: "Copy thread ID" })
    )

    expect(writeText).toHaveBeenCalledWith(expectedId)
  })
})
