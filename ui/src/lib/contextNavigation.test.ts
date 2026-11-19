/** @vitest-environment jsdom */
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router"
import { beforeEach, expect, it } from "vitest"

import { ContextNavigation } from "./contextNavigation"

beforeEach(() => window.sessionStorage.clear())

class NavigationScenario {
  readonly router
  readonly navigation
  readonly ready

  constructor(href = "/agents/thread-1") {
    const root = createRootRoute()
    const page = createRoute({ getParentRoute: () => root, path: "$" })
    this.router = createRouter({
      isServer: false,
      routeTree: root.addChildren([page]),
      history: createMemoryHistory({ initialEntries: [href] }),
      parseSearch: (search) => Object.fromEntries(new URLSearchParams(search)),
    })
    this.navigation = ContextNavigation.for(this.router)
    this.ready = this.router.load()
  }

  options(kind: Parameters<ContextNavigation["returnOptions"]>[0] = "review") {
    return this.navigation.returnOptions(kind, this.router.state.location)
  }

  async open(
    href: string,
    state?: ReturnType<NavigationScenario["options"]>["state"]
  ) {
    await this.ready
    await this.router.navigate({ href, state })
  }

  async back(
    kind: Parameters<ContextNavigation["returnOptions"]>[0] = "review"
  ) {
    this.options(kind).returnToOrigin()
    await this.router.load()
  }
}

it("returns to the exact filtered list history entry", async () => {
  const list = "/agents/reviews?tab=to-review&repo=acme%2Fapi&q=fix#selected"
  const scenario = new NavigationScenario(list)
  await scenario.open("/agents/reviews?pr=acme%2Fapi%231")
  expect(scenario.options().label).toBe("Back to pull requests")
  await scenario.back()
  expect(scenario.router.state.location.href).toBe(list)
  expect(scenario.router.state.location.state.__TSR_index).toBe(0)
})

it("returns to the original chat after closing PR details", async () => {
  const chat = "/agents/thread-1"
  const list = "/agents/reviews?tab=mine"
  const scenario = new NavigationScenario(chat)
  await scenario.open(list)
  await scenario.open(`${list}&pr=acme%2Fapi%231`)
  await scenario.open(list)
  expect(scenario.options("review-exit").label).toBe("Back to thread")
  await scenario.back("review-exit")
  expect(scenario.router.state.location.href).toBe(chat)
  expect(scenario.router.state.location.state.__TSR_index).toBe(0)
})

it("returns between pull requests without creating a back-button loop", async () => {
  const scenario = new NavigationScenario()
  await scenario.open("/agents/reviews?pr=acme%2Fapi%231")
  await scenario.open("/agents/reviews?pr=acme%2Fapi%232")
  expect(scenario.options().label).toBe("Back to acme/api#1")
  await scenario.back()
  expect(scenario.options().label).toBe("Back to thread")
  await scenario.back()
  expect(scenario.router.state.location.pathname).toBe("/agents/thread-1")
})

it("closes all Settings sections to their entry origin and preserves the review origin", async () => {
  const scenario = new NavigationScenario()
  await scenario.open("/agents/reviews?pr=acme%2Fapi%231")
  await scenario.open("/my-settings")
  await scenario.open("/my-settings/git")
  await scenario.open("/review/styles")
  await scenario.open("/admin")
  await scenario.back("settings")
  expect(scenario.router.state.location.href).toBe(
    "/agents/reviews?pr=acme%2Fapi%231"
  )
  expect(scenario.options().label).toBe("Back to thread")
  await scenario.back()
  expect(scenario.router.state.location.pathname).toBe("/agents/thread-1")
})

it("keeps a queue's list origin while advancing across PRs", async () => {
  const scenario = new NavigationScenario("/agents/reviews?tab=to-review&q=fix")
  await scenario.open("/agents/reviews?pr=acme%2Fapi%231&queue=first")
  await scenario.open(
    "/agents/reviews?pr=acme%2Fapi%232&queue=first",
    scenario.options().state
  )
  expect(scenario.options().label).toBe("Back to pull requests")
  await scenario.back()
  expect(scenario.router.state.location.href).toBe(
    "/agents/reviews?tab=to-review&q=fix"
  )
})

it("preserves origins through search replacement and forward navigation", async () => {
  const scenario = new NavigationScenario()
  await scenario.open("/agents/reviews?pr=acme%2Fapi%231")
  await scenario.router.navigate({
    href: "/agents/reviews?pr=acme%2Fapi%231&view=diff",
    replace: true,
  })
  expect(scenario.options().label).toBe("Back to thread")
  await scenario.open("/my-settings")
  await scenario.back("settings")
  scenario.router.history.forward()
  await scenario.router.load()
  await scenario.back("settings")
  expect(scenario.options().label).toBe("Back to thread")
})

it("gives a direct PR link an honest Pull requests fallback", async () => {
  const scenario = new NavigationScenario("/agents/reviews?pr=acme%2Fapi%231")
  expect(scenario.options().label).toBe("Pull requests")
  expect(scenario.options().back).toBe(false)
  await scenario.back()
  expect(scenario.router.state.location.pathname).toBe("/agents/reviews")
})
