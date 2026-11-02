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
import { useDesktopExperience } from "./experience"

const state = vi.hoisted(() => ({ login: "alice", navigate: vi.fn() }))
vi.mock("./session", () => ({
  useSession: () => ({ data: { login: state.login } }),
}))
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => state.navigate }))

afterEach(() => {
  cleanup()
  localStorage.clear()
  state.login = "alice"
  state.navigate.mockClear()
  Reflect.deleteProperty(window, "openSweDesktop")
})

it("defaults desktop to Work, persists a switch locally, and isolates users", () => {
  Object.defineProperty(window, "openSweDesktop", {
    configurable: true,
    value: {},
  })
  const first = renderHook(() => useDesktopExperience())
  expect(first.result.current.experience).toBe("agent_workspace")
  act(() => first.result.current.select("coding"))
  first.unmount()
  const second = renderHook(() => useDesktopExperience())
  expect(second.result.current.experience).toBe("coding")
  state.login = "bob"
  second.rerender()
  expect(second.result.current.experience).toBe("agent_workspace")
  state.login = "alice"
  second.rerender()
  expect(second.result.current.experience).toBe("coding")
})

it("opens the selected home and exposes the selected label in browser and desktop", () => {
  render(<ExperienceSelector />)
  expect(
    screen.getByRole("button", { name: "Work" }).getAttribute("aria-pressed")
  ).toBe("true")
  fireEvent.click(screen.getByRole("button", { name: "Coding" }))
  expect(
    screen.getByRole("button", { name: "Coding" }).getAttribute("aria-pressed")
  ).toBe("true")
  expect(state.navigate).toHaveBeenCalledWith({ to: "/agents", search: {} })
  Reflect.deleteProperty(window, "openSweDesktop")
  const browser = renderHook(() => useDesktopExperience())
  expect(browser.result.current.experience).toBe("coding")
})
