/** @vitest-environment jsdom */

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@langchain/macaw-components/DropdownMenu"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import type { DesktopLegacyLocalThread } from "@/desktop"
import type { AgentPullRequest, AgentThread } from "@/features/agents/lib/types"
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

const localThread: DesktopLegacyLocalThread = {
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

function renderMenu(props: {
  thread: AgentThread | null
  localThread?: DesktopLegacyLocalThread
}) {
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
          onToggleArchived={vi.fn()}
          onDelete={vi.fn()}
        />
      </DropdownMenuContent>
    </DropdownMenu>
  )
  fireEvent.keyDown(screen.getByRole("button", { name: "Thread actions" }), {
    key: "Enter",
  })
}

describe("Thread menu", () => {
  it("links the primary and PR repositories without duplicates", async () => {
    const pr: AgentPullRequest = {
      repoFullName: thread.repoFullName,
      number: 1,
      title: "Change",
      state: "open",
      headRef: "feature",
      baseRef: "main",
      url: "https://github.com/langchain-ai/open-swe/pull/1",
      author: null,
      authorAvatarUrl: null,
      createdAt: null,
      diffStats: { files: 1, additions: 1, deletions: 0 },
    }
    renderMenu({
      thread: {
        ...thread,
        pullRequests: [pr, { ...pr, repoFullName: "langchain-ai/docs" }, pr],
      },
    })
    const primary = await screen.findAllByRole("menuitem", {
      name: thread.repoFullName,
    })
    expect(primary).toHaveLength(1)
    expect(primary[0]?.getAttribute("href")).toBe(
      `https://github.com/${thread.repoFullName}`
    )
    expect(
      screen
        .getByRole("menuitem", { name: "langchain-ai/docs" })
        .getAttribute("href")
    ).toBe("https://github.com/langchain-ai/docs")
  })

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

    renderMenu(props)
    fireEvent.click(
      await screen.findByRole("menuitem", { name: "Copy thread ID" })
    )

    expect(writeText).toHaveBeenCalledWith(expectedId)
  })
})
