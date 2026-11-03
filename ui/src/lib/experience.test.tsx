/** @vitest-environment jsdom */
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
} from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { ExperienceSelector } from "@/components/ExperienceSelector"
import { useExperience } from "./experience"

const state = vi.hoisted(() => ({
  login: "alice",
  href: "/agents",
  navigate: vi.fn(),
}))
vi.mock("./session", () => ({
  useSession: () => ({ data: { login: state.login, user_id: state.login } }),
}))
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => state.navigate,
  useRouterState: () => state.href,
  useRouter: () => ({
    options: {
      parseSearch: (search: string) =>
        Object.fromEntries(new URLSearchParams(search)),
    },
  }),
}))

afterEach(() => {
  cleanup()
  localStorage.clear()
  state.login = "alice"
  state.href = "/agents"
  state.navigate.mockClear()
  Reflect.deleteProperty(window, "openSweDesktop")
})

it("defaults desktop to Work, persists a switch locally, and isolates users", () => {
  Object.defineProperty(window, "openSweDesktop", {
    configurable: true,
    value: {},
  })
  const first = renderHook(() => useExperience())
  expect(first.result.current.experience).toBe("agent_workspace")
  act(() => first.result.current.select("coding"))
  first.unmount()
  const second = renderHook(() => useExperience())
  expect(second.result.current.experience).toBe("coding")
  state.login = "bob"
  second.rerender()
  expect(second.result.current.experience).toBe("agent_workspace")
  state.login = "alice"
  second.rerender()
  expect(second.result.current.experience).toBe("coding")
})

it("restores each mode's last route without disturbing an active mode or another user", () => {
  state.login = "route-user"
  state.href = "/agents/home-thread?subagent=worker"
  const view = render(<ExperienceSelector />)
  fireEvent.click(screen.getByRole("button", { name: "Home" }))
  expect(state.navigate).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: "Code" }))
  expect(state.navigate).toHaveBeenLastCalledWith({
    to: "/agents",
    search: {},
    hash: undefined,
  })
  state.href = "/agents/code-thread"
  view.rerender(<ExperienceSelector />)
  fireEvent.click(screen.getByRole("button", { name: "Home" }))
  expect(state.navigate).toHaveBeenLastCalledWith({
    to: "/agents/home-thread",
    search: { subagent: "worker" },
    hash: undefined,
  })
  state.href = "/agents/home-thread?subagent=worker"
  view.rerender(<ExperienceSelector />)
  fireEvent.click(screen.getByRole("button", { name: "Code" }))
  expect(state.navigate).toHaveBeenLastCalledWith({
    to: "/agents/code-thread",
    search: {},
    hash: undefined,
  })
  state.login = "other-route-user"
  view.rerender(<ExperienceSelector />)
  fireEvent.click(screen.getByRole("button", { name: "Code" }))
  expect(state.navigate).toHaveBeenLastCalledWith({
    to: "/agents",
    search: {},
    hash: undefined,
  })
})

it("opens the selected home and exposes the selected label in browser and desktop", () => {
  const onNavigate = vi.fn()
  render(<ExperienceSelector onNavigate={onNavigate} />)
  expect(
    screen.getByRole("button", { name: "Home" }).getAttribute("aria-pressed")
  ).toBe("true")
  expect(
    screen.getByRole("button", { name: "Code" }).getAttribute("aria-pressed")
  ).toBe("false")
  fireEvent.click(screen.getByRole("button", { name: "Code" }))
  expect(
    screen.getByRole("button", { name: "Code" }).getAttribute("aria-pressed")
  ).toBe("true")
  expect(
    screen.getByRole("button", { name: "Home" }).getAttribute("aria-pressed")
  ).toBe("false")
  expect(onNavigate).toHaveBeenCalledOnce()
  expect(state.navigate).toHaveBeenCalledWith({
    to: "/agents",
    search: {},
    hash: undefined,
  })
  Reflect.deleteProperty(window, "openSweDesktop")
  const browser = renderHook(() => useExperience())
  expect(browser.result.current.experience).toBe("coding")
})
