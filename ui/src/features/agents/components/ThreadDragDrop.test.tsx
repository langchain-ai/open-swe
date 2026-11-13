/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { createRef, useContext, useEffect } from "react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { ThreadDragDrop } from "./ThreadDragDrop"
import { SidebarThreadDragPreviewContext } from "./ThreadDragDrop.utils"
import { SortableThreadList } from "./SortableThreadList"
import { cloudSidebarThread } from "@/features/agents/lib/sidebarThreads"

vi.mock("@/features/agents/components/SidebarThreadSelection", () => ({
  useSidebarThreadSelection: () => ({
    selected: new Map([
      ["first", "First"],
      ["second", "Second"],
    ]),
  }),
}))

const animations: Array<{ onfinish: (() => void) | null; cancel: () => void }> =
  []
const originalAnimate = Object.getOwnPropertyDescriptor(
  Element.prototype,
  "animate"
)

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    }
  )
  animations.length = 0
  vi.stubGlobal("matchMedia", () => ({ matches: false }))
  Object.defineProperty(Element.prototype, "animate", {
    configurable: true,
    value: vi.fn(() => {
      const animation = {
        onfinish: null as (() => void) | null,
        cancel: vi.fn(),
      }
      animations.push(animation)
      return animation
    }),
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  if (originalAnimate)
    Object.defineProperty(Element.prototype, "animate", originalAnimate)
  else Reflect.deleteProperty(Element.prototype, "animate")
})

function Row({
  id,
  pinned = false,
  archived = false,
}: {
  id: string
  pinned?: boolean
  archived?: boolean
}) {
  const register = useContext(SidebarThreadDragPreviewContext)
  useEffect(() => {
    register(id, <div>{id}</div>)
    return () => register(id, null)
  }, [id, register])
  return (
    <div data-sidebar-thread-row>
      <a
        href={`/agents/${id}`}
        draggable
        data-selectable-thread={id}
        data-thread-title={id}
        data-thread-pinned={pinned}
        data-thread-archived={archived}
      >
        {id}
      </a>
    </div>
  )
}

it.each(["pin", "archive"] as const)(
  "applies %s to the dragged selection and hides its original rows",
  (action) => {
    const onAction = vi.fn()
    render(
      <ThreadDragDrop onAction={onAction}>
        <Row id="first" />
        <Row id="second" />
        <Row id="other" />
      </ThreadDragDrop>
    )
    const first = screen.getByRole("link", { name: "first" })
    const second = screen.getByRole("link", { name: "second" })
    const other = screen.getByRole("link", { name: "other" })
    fireEvent.dragStart(first, {
      clientY: 10,
      dataTransfer: { setData: vi.fn(), setDragImage: vi.fn() },
    })
    expect(first.parentElement?.style.opacity).toBe("0")
    expect(second.parentElement?.style.opacity).toBe("0")
    expect(other.parentElement?.style.opacity).toBe("")
    fireEvent.drop(
      screen.getByRole("region", {
        name: action === "pin" ? /to pin$/i : /to archive$/i,
      })
    )
    expect(onAction.mock.calls).toEqual([
      [
        expect.arrayContaining([
          { threadId: "first", action },
          { threadId: "second", action },
        ]),
      ],
    ])
    expect(onAction.mock.calls[0]?.[0]).toHaveLength(2)
    expect(first.parentElement?.style.opacity).toBe("")
    expect(second.parentElement?.style.opacity).toBe("")
  }
)

it("returns every selected row before revealing the originals after cancellation", () => {
  const onAction = vi.fn()
  render(
    <ThreadDragDrop onAction={onAction}>
      <Row id="first" />
      <Row id="second" />
    </ThreadDragDrop>
  )
  const first = screen.getByRole("link", { name: "first" })
  const second = screen.getByRole("link", { name: "second" })
  fireEvent.dragStart(first, {
    dataTransfer: { setData: vi.fn(), setDragImage: vi.fn() },
  })
  fireEvent.dragEnd(first)
  expect(first.parentElement?.style.opacity).toBe("0")
  expect(second.parentElement?.style.opacity).toBe("0")
  act(() => animations.at(-1)?.onfinish?.())
  expect(first.parentElement?.style.opacity).toBe("")
  expect(second.parentElement?.style.opacity).toBe("")
  expect(onAction).not.toHaveBeenCalled()
})

it("drags an unselected row on its own", () => {
  const onAction = vi.fn()
  render(
    <ThreadDragDrop onAction={onAction}>
      <Row id="first" />
      <Row id="second" />
      <Row id="other" />
    </ThreadDragDrop>
  )
  fireEvent.dragStart(screen.getByRole("link", { name: "other" }), {
    dataTransfer: { setData: vi.fn(), setDragImage: vi.fn() },
  })
  fireEvent.drop(screen.getByRole("region", { name: /to pin$/i }))
  expect(onAction.mock.calls).toEqual([
    [[{ threadId: "other", action: "pin" }]],
  ])
})

it("reorders a single pinned thread while keeping unpin and archive available", () => {
  const onAction = vi.fn()
  const onReorder = vi.fn()
  const pinnedSectionRef = createRef<HTMLElement>()
  let pinnedBottom = 200
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    function (this: HTMLElement) {
      if (this === pinnedSectionRef.current)
        return new DOMRect(0, 0, 200, pinnedBottom)
      if (this.getAttribute("aria-label") === "Drag to archive")
        return new DOMRect(0, 500, 200, 100)
      return new DOMRect(0, 0, 200, 600)
    }
  )
  const items = ["other", "last"].map((id) =>
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
  render(
    <ThreadDragDrop onAction={onAction} pinnedSectionRef={pinnedSectionRef}>
      <div data-testid="sidebar-viewport" style={{ overflowY: "auto" }}>
        <section ref={pinnedSectionRef}>
          <SortableThreadList
            items={items}
            threadIds={["other", "hidden", "last"]}
            disabled={false}
            onReorder={onReorder}
            renderRow={(item) => <Row id={item.id} pinned />}
          />
        </section>
      </div>
    </ThreadDragDrop>
  )
  const other = screen.getByRole("link", { name: "other" })
  const last = screen.getByRole("link", { name: "last" })
  fireEvent.dragStart(last, {
    dataTransfer: { setData: vi.fn(), setDragImage: vi.fn() },
  })
  expect(screen.queryByRole("region", { name: /to pin$/i })).toBeNull()
  expect(screen.getByRole("region", { name: /to unpin$/i })).toBeTruthy()
  const unpin = screen.getByRole("region", { name: /to unpin$/i })
  expect(unpin.style.top).toBe("200px")
  expect(unpin.style.height).toBe("300px")
  pinnedBottom = 350
  fireEvent.scroll(screen.getByTestId("sidebar-viewport"))
  expect(unpin.style.top).toBe("350px")
  expect(unpin.style.height).toBe("150px")
  fireEvent.dragOver(other, { clientY: -1, dataTransfer: {} })
  fireEvent.drop(other)
  expect(onReorder).toHaveBeenCalledWith(["last", "other", "hidden"])
  expect(onAction).not.toHaveBeenCalled()

  fireEvent.dragStart(last, {
    dataTransfer: { setData: vi.fn(), setDragImage: vi.fn() },
  })
  fireEvent.drop(screen.getByRole("region", { name: /to unpin$/i }))
  expect(onAction).toHaveBeenCalledWith([{ threadId: "last", action: "unpin" }])

  fireEvent.dragStart(last, {
    dataTransfer: { setData: vi.fn(), setDragImage: vi.fn() },
  })
  fireEvent.drop(screen.getByRole("region", { name: /to archive$/i }))
  expect(onAction).toHaveBeenCalledWith([
    { threadId: "last", action: "archive" },
  ])
})

it("restores only the relevant state for each thread in a mixed selection", () => {
  const onAction = vi.fn()
  render(
    <ThreadDragDrop onAction={onAction}>
      <Row id="first" pinned archived />
      <Row id="second" archived />
    </ThreadDragDrop>
  )
  fireEvent.dragStart(screen.getByRole("link", { name: "first" }), {
    dataTransfer: { setData: vi.fn(), setDragImage: vi.fn() },
  })
  fireEvent.drop(
    screen.getByRole("region", { name: /to unpin and unarchive$/i })
  )
  expect(onAction.mock.calls).toEqual([
    [
      expect.arrayContaining([
        { threadId: "first", action: "unpin" },
        { threadId: "first", action: "unarchive" },
        { threadId: "second", action: "unarchive" },
      ]),
    ],
  ])
  expect(onAction.mock.calls[0]?.[0]).toHaveLength(3)
})
