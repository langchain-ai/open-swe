/** @vitest-environment jsdom */

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"

import { useTraceFlight } from "./launcher"

const track = vi.hoisted(() => vi.fn())
const season = vi.hoisted(() => ({
  fetch: (): Promise<null> => Promise.resolve(null),
}))

vi.mock("@/lib/analytics", () => ({ productAnalytics: { track } }))
vi.mock("./season", () => ({
  seasonQuery: {
    queryKey: ["trace-flight", "season"],
    queryFn: () => season.fetch(),
  },
}))
vi.mock("@/lib/theme", () => ({ useTheme: () => ({ resolvedTheme: "light" }) }))
// The game itself draws to a canvas jsdom lacks; a button that leaves stands in for it.
vi.mock("../../view/TraceFlight", () => ({
  TraceFlight: (props: { onDepart: () => void; onExit: () => void }) => (
    <div role="application" aria-label="Game" tabIndex={0}>
      <button type="button" onClick={props.onDepart}>
        Depart
      </button>
      <button type="button" onClick={props.onExit}>
        Leave
      </button>
    </div>
  ),
}))
vi.stubGlobal("matchMedia", () => ({
  matches: false,
  addEventListener: () => {},
  removeEventListener: () => {},
}))

function Page(props: { palette?: boolean }) {
  const traceFlight = useTraceFlight()
  return (
    <div className="relative">
      <button type="button">Sidebar</button>
      {props.palette && (
        <div role="dialog" aria-label="Palette">
          <input aria-label="Search" />
        </div>
      )}
      <div inert={traceFlight.playing}>{traceFlight.logo}</div>
      {traceFlight.game}
    </div>
  )
}

function clickLogoAt(times: readonly number[]) {
  const now = vi.spyOn(performance, "now")
  const logo = screen.getByRole("button", { name: "Robin" })
  for (const at of times) {
    now.mockReturnValue(at)
    fireEvent.click(logo)
  }
  now.mockRestore()
}

function renderPage() {
  const client = new QueryClient()
  const page = (palette: boolean) => (
    <QueryClientProvider client={client}>
      <Page palette={palette} />
    </QueryClientProvider>
  )
  const { rerender } = render(page(false))
  return {
    openPalette: () => rerender(page(true)),
    closePalette: () => rerender(page(false)),
  }
}

afterEach(() => {
  cleanup()
  track.mockReset()
  season.fetch = () => Promise.resolve(null)
})

it("opens on four quick clicks and hands focus back to the logo on leaving", async () => {
  renderPage()
  clickLogoAt([0, 150, 300, 450])
  expect(track).toHaveBeenCalledWith("trace_flight_opened", {})

  const dialog = await screen.findByRole("dialog", { name: "Trace Flight" })
  const leave = await within(dialog).findByRole("button", { name: "Leave" })
  await act(async () => fireEvent.click(leave))

  expect(screen.queryByRole("dialog", { name: "Trace Flight" })).toBeNull()
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "Robin" })
  )
})

it("stays closed when the clicks are spread out", () => {
  renderPage()
  clickLogoAt([0, 400, 800, 1200, 1600])
  expect(screen.queryByRole("dialog", { name: "Trace Flight" })).toBeNull()
  expect(track).not.toHaveBeenCalled()
})

it("leaves on Esc while the season is still loading", async () => {
  season.fetch = () => new Promise(() => {})
  renderPage()
  clickLogoAt([0, 150, 300, 450])
  const dialog = await screen.findByRole("dialog", { name: "Trace Flight" })
  expect(document.activeElement).toBe(dialog)

  await act(async () => fireEvent.keyDown(dialog, { key: "Escape" }))

  expect(screen.queryByRole("dialog", { name: "Trace Flight" })).toBeNull()
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "Robin" })
  )
})

it("takes focus back to the game from the page, but not from a layer opened over it", async () => {
  renderPage()
  clickLogoAt([0, 150, 300, 450])
  const dialog = await screen.findByRole("dialog", { name: "Trace Flight" })
  const game = await within(dialog).findByRole("application", { name: "Game" })

  act(() => screen.getByRole("button", { name: "Sidebar" }).focus())
  expect(document.activeElement).toBe(game)

  act(() => (document.activeElement as HTMLElement).blur())
  const pageKeys = vi.fn()
  window.addEventListener("keydown", pageKeys)
  fireEvent.keyDown(document.body, { key: "k", metaKey: true })
  window.removeEventListener("keydown", pageKeys)
  expect(pageKeys).not.toHaveBeenCalled()
  expect(document.activeElement).toBe(game)
})

it("lets a layer opened over the game keep focus, and takes it back when the layer closes", async () => {
  const page = renderPage()
  clickLogoAt([0, 150, 300, 450])
  const dialog = await screen.findByRole("dialog", { name: "Trace Flight" })
  const game = await within(dialog).findByRole("application", { name: "Game" })

  act(() => page.openPalette())
  const search = screen.getByRole("textbox", { name: "Search" })
  act(() => search.focus())
  expect(document.activeElement).toBe(search)

  // Closing without a trigger to return to leaves focus on the page, as the command palette does.
  await act(async () => page.closePalette())
  expect(document.activeElement).toBe(game)
})

it("keeps the logo in place until the bird leaps out of it, so a first open still loading isn't blank", async () => {
  renderPage()
  const logoHidden = () =>
    screen
      .getByRole("button", { name: "Robin" })
      .firstElementChild?.classList.contains("invisible")
  clickLogoAt([0, 150, 300, 450])
  const dialog = await screen.findByRole("dialog", { name: "Trace Flight" })
  expect(logoHidden()).toBe(false)

  const depart = await within(dialog).findByRole("button", { name: "Depart" })
  act(() => fireEvent.click(depart))
  expect(logoHidden()).toBe(true)

  await act(async () =>
    fireEvent.click(within(dialog).getByRole("button", { name: "Leave" }))
  )
  expect(logoHidden()).toBe(false)
})
