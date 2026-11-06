/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { SortableThreadList } from "./SortableThreadList"
import { cloudSidebarThread } from "@/features/agents/lib/sidebarThreads"

afterEach(cleanup)

const items = ["first", "second", "third"].map((id) =>
  cloudSidebarThread({
    id,
    title: id,
    repo: "",
    repoFullName: "",
    branch: "",
    model: "",
    status: "idle",
    viewed: true,
    createdAt: 1,
    updatedAt: 1,
    messages: [],
  })
)

it("moves a focused pin past its visible neighbor without dropping hidden pins", () => {
  const onReorder = vi.fn()
  render(
    <SortableThreadList
      items={items}
      threadIds={["first", "hidden", "second", "third"]}
      disabled={false}
      onReorder={onReorder}
      renderRow={(item) => <a href={`/agents/${item.id}`}>{item.title}</a>}
    />
  )
  fireEvent.keyDown(screen.getByRole("link", { name: "second" }), {
    key: "ArrowUp",
    altKey: true,
  })
  expect(onReorder).toHaveBeenCalledWith(["second", "first", "hidden", "third"])
  expect(screen.getByText("second moved to position 1 of 4.")).toBeTruthy()
})

it("drops a dragged pin only while its destination is still hovered", () => {
  const onReorder = vi.fn()
  const { container } = render(
    <SortableThreadList
      items={items}
      threadIds={items.map((item) => item.id)}
      disabled={false}
      onReorder={onReorder}
      renderRow={(item) => <a href={`/agents/${item.id}`}>{item.title}</a>}
    />
  )
  const first = screen.getByRole("link", { name: "first" }).parentElement!
  const third = screen.getByRole("link", { name: "third" }).parentElement!
  fireEvent.drop(first)
  expect(onReorder).not.toHaveBeenCalled()
  fireEvent.dragStart(third, { dataTransfer: { setData: vi.fn() } })
  fireEvent.dragOver(first, { clientY: -1, dataTransfer: {} })
  fireEvent.dragLeave(first, { relatedTarget: document.body })
  fireEvent.drop(first)
  expect(onReorder).not.toHaveBeenCalled()
  fireEvent.dragOver(first, { clientY: -1, dataTransfer: {} })
  fireEvent(
    first,
    new MouseEvent("dragleave", {
      bubbles: true,
      relatedTarget: first.firstElementChild,
    })
  )
  vi.spyOn(first, "getBoundingClientRect").mockReturnValue(
    new DOMRect(0, 0, 200, 32)
  )
  fireEvent(
    screen.getByRole("link", { name: "first" }),
    new MouseEvent("dragleave", {
      bubbles: true,
      relatedTarget: null,
      clientX: 20,
      clientY: 10,
    })
  )
  fireEvent.drop(first)
  expect(onReorder).toHaveBeenCalledWith(["third", "first", "second"])
  expect(container.querySelectorAll('[draggable="true"]')).toHaveLength(3)
})

it("blocks reordering while an earlier change is saving", () => {
  const onReorder = vi.fn()
  render(
    <SortableThreadList
      items={items}
      threadIds={items.map((item) => item.id)}
      disabled
      onReorder={onReorder}
      renderRow={(item) => <a href={`/agents/${item.id}`}>{item.title}</a>}
    />
  )
  fireEvent.keyDown(screen.getByRole("link", { name: "second" }), {
    key: "ArrowDown",
    altKey: true,
  })
  expect(onReorder).not.toHaveBeenCalled()
})
