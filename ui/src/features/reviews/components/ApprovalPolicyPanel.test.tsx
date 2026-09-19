// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import {
  api,
  ApiError,
  type PolicyDefinition,
  type PolicySettingsView,
} from "@/lib/api"
import { ApprovalPolicyPanel } from "./ApprovalPolicyPanel"

vi.mock("@/lib/profile", () => ({
  useRepos: () => ({
    data: {
      installations: [],
      repositories: [{ full_name: "acme/widgets", private: true }],
    },
    isLoading: false,
  }),
}))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const SHARED_POLICY: PolicyDefinition = {
  rules: {
    max_risk_score: 2,
    minimum_confidence: "medium",
    required_checks: ["build"],
    human_review_paths: ["security/**"],
  },
  criteria_markdown: "## Tests\nAll tests must pass.",
}

function view(overrides: Partial<PolicySettingsView> = {}): PolicySettingsView {
  return {
    repository: null,
    policy: SHARED_POLICY,
    shared_policy: SHARED_POLICY,
    effective_rules: SHARED_POLICY.rules,
    effective_version: "shared-v1",
    revision: "shared-r1",
    updated_by: "octocat",
    updated_at: "2026-09-19T12:00:00Z",
    can_edit: true,
    ...overrides,
  }
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <ApprovalPolicyPanel />
    </QueryClientProvider>
  )
  return client
}

it("saves structured shared policy changes and resets to the built-in default", async () => {
  vi.spyOn(api, "getReviewApprovalPolicy").mockResolvedValue(view())
  const save = vi
    .spyOn(api, "saveReviewApprovalPolicy")
    .mockImplementation(async (_repository, policy) =>
      view({
        policy,
        revision: policy ? "shared-r2" : null,
        effective_version: policy ? "shared-v2" : "built-in-v1",
      })
    )
  renderPanel()

  fireEvent.change(await screen.findByLabelText("Maximum risk score"), {
    target: { value: "1" },
  })
  const checks = screen.getByLabelText("Required checks")
  fireEvent.change(checks, { target: { value: "build" } })
  fireEvent.change(checks, { target: { value: "build\n" } })
  expect(checks).toHaveProperty("value", "build\n")
  fireEvent.change(checks, { target: { value: "build\nsecurity-scan" } })
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }))

  await screen.findByText("Policy saved.")
  expect(save).toHaveBeenNthCalledWith(
    1,
    null,
    {
      rules: {
        max_risk_score: 1,
        minimum_confidence: "medium",
        required_checks: ["build", "security-scan"],
        human_review_paths: ["security/**"],
      },
      criteria_markdown: "## Tests\nAll tests must pass.",
    },
    "shared-v1"
  )

  fireEvent.click(screen.getByRole("button", { name: "Reset policy" }))
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2))
  expect(save).toHaveBeenNthCalledWith(2, null, null, "shared-v2")
  expect(
    await screen.findByText("Policy reset to the built-in default.")
  ).toBeTruthy()
})

it("keeps an edited draft after a save error and tells stale editors to reload", async () => {
  vi.spyOn(api, "getReviewApprovalPolicy").mockResolvedValue(view())
  vi.spyOn(api, "saveReviewApprovalPolicy").mockRejectedValue(
    new ApiError(409, "Version conflict")
  )
  renderPanel()

  fireEvent.change(
    await screen.findByLabelText("Additional criteria (Markdown)"),
    {
      target: { value: "## Security\nInspect auth changes." },
    }
  )
  expect(
    screen.getByLabelText("Additional criteria (Markdown)")
  ).toHaveProperty("value", "## Security\nInspect auth changes.")
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }))

  expect((await screen.findByRole("alert")).textContent).toContain(
    "This policy changed since you loaded it. Reload before saving again."
  )
  expect(
    screen.getByLabelText("Additional criteria (Markdown)")
  ).toHaveProperty("value", "## Security\nInspect auth changes.")
})

it("keeps the draft and its original version when a background refresh finds a newer policy", async () => {
  const read = vi
    .spyOn(api, "getReviewApprovalPolicy")
    .mockResolvedValue(view())
  const save = vi
    .spyOn(api, "saveReviewApprovalPolicy")
    .mockRejectedValue(new ApiError(409, "Version conflict"))
  const client = renderPanel()

  fireEvent.change(
    await screen.findByLabelText("Additional criteria (Markdown)"),
    {
      target: { value: "## Local draft\nKeep this work." },
    }
  )
  const externalView = view({
    policy: {
      ...SHARED_POLICY,
      criteria_markdown: "## External change\nSaved elsewhere.",
    },
    revision: "shared-r2",
    effective_version: "shared-v2",
  })
  read.mockResolvedValue(externalView)
  client.setQueryData(["review-approval-policy", null], externalView)

  expect(
    screen.getByLabelText("Additional criteria (Markdown)")
  ).toHaveProperty("value", "## Local draft\nKeep this work.")
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }))
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "This policy changed since you loaded it. Reload before saving again."
  )
  expect(save.mock.calls[0]?.[2]).toBe("shared-v1")
  expect(
    screen.getByLabelText("Additional criteria (Markdown)")
  ).toHaveProperty("value", "## Local draft\nKeep this work.")
  fireEvent.click(screen.getByRole("button", { name: "Reload policy" }))
  expect(await screen.findByText("Policy reloaded.")).toBeTruthy()
  expect(
    screen.getByLabelText("Additional criteria (Markdown)")
  ).toHaveProperty("value", "## External change\nSaved elsewhere.")
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }))
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2))
  expect(save.mock.calls[1]?.[2]).toBe("shared-v2")
})

it("shows bounded guidance instead of echoing a large validation response", async () => {
  vi.spyOn(api, "getReviewApprovalPolicy").mockResolvedValue(view())
  vi.spyOn(api, "saveReviewApprovalPolicy").mockRejectedValue(
    new ApiError(
      422,
      JSON.stringify([
        {
          loc: ["body", "policy", "criteria_markdown"],
          msg: "Shared approval policy must contain 1–20 named ## sections",
          input: "x".repeat(10_000),
        },
      ])
    )
  )
  renderPanel()

  fireEvent.click(await screen.findByRole("button", { name: "Save policy" }))
  const alert = await screen.findByRole("alert")
  expect(alert.textContent).toContain(
    "Shared approval policy must contain 1–20 named ## sections"
  )
  expect(alert.textContent?.length).toBeLessThan(300)
  expect(alert.textContent).not.toContain("xxxxxxxx")
})

it("locks the editor during reload and preserves the draft when reload fails", async () => {
  let rejectReload: ((reason: Error) => void) | undefined
  vi.spyOn(api, "getReviewApprovalPolicy")
    .mockResolvedValueOnce(view())
    .mockImplementationOnce(
      () =>
        new Promise<PolicySettingsView>((_resolve, reject) => {
          rejectReload = reject
        })
    )
  renderPanel()

  const criteria = await screen.findByLabelText(
    "Additional criteria (Markdown)"
  )
  fireEvent.change(criteria, {
    target: { value: "## Unsaved draft\nKeep this text." },
  })
  fireEvent.click(screen.getByRole("button", { name: "Reload policy" }))

  expect(criteria.matches(":disabled")).toBe(true)
  expect(
    screen.getByRole("button", { name: "Save policy" }).hasAttribute("disabled")
  ).toBe(true)
  expect(
    screen
      .getByRole("button", { name: "Reset policy" })
      .hasAttribute("disabled")
  ).toBe(true)
  expect(
    screen.getByRole("button", { name: "Reloading…" }).hasAttribute("disabled")
  ).toBe(true)

  rejectReload?.(new Error("Offline"))
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Offline"
  )
  expect(criteria.matches(":disabled")).toBe(false)
  expect(criteria).toHaveProperty("value", "## Unsaved draft\nKeep this text.")
})

it("shows policy metadata but disables editing for a read-only user", async () => {
  vi.spyOn(api, "getReviewApprovalPolicy").mockResolvedValue(
    view({ can_edit: false })
  )
  renderPanel()

  expect(
    await screen.findByText("You have read-only access to this policy.")
  ).toBeTruthy()
  expect(screen.getByLabelText("Maximum risk score").matches(":disabled")).toBe(
    true
  )
  expect(
    screen.getByRole("button", { name: "Save policy" }).hasAttribute("disabled")
  ).toBe(true)
  expect(screen.getByText(/Saved by octocat/)).toBeTruthy()
  expect(screen.getByText(/shared-v1/)).toBeTruthy()
})

it("shortens long versions and disables reset when the scope has no saved policy", async () => {
  const version = "1234567890abcdef1234567890abcdef"
  vi.spyOn(api, "getReviewApprovalPolicy").mockResolvedValue(
    view({ policy: null, revision: null, effective_version: version })
  )
  renderPanel()

  const displayedVersion = await screen.findByText("1234567890ab")
  expect(displayedVersion.getAttribute("title")).toBe(version)
  expect(
    screen
      .getByRole("button", { name: "Reset policy" })
      .hasAttribute("disabled")
  ).toBe(true)
})

it("explains inherited shared requirements and the repository's stricter effective policy", async () => {
  const repositoryPolicy: PolicyDefinition = {
    rules: {
      max_risk_score: 1,
      minimum_confidence: "high",
      required_checks: ["security-scan"],
      human_review_paths: ["payments/**"],
    },
    criteria_markdown: "",
  }
  vi.spyOn(api, "getReviewApprovalPolicy").mockImplementation(
    async (repository) =>
      repository
        ? view({
            repository,
            policy: repositoryPolicy,
            effective_rules: {
              max_risk_score: 1,
              minimum_confidence: "high",
              required_checks: ["build", "security-scan"],
              human_review_paths: ["security/**", "payments/**"],
            },
            effective_version: "repo-effective-v3",
            revision: "repo-r2",
          })
        : view()
  )
  renderPanel()

  fireEvent.change(await screen.findByLabelText("Policy scope"), {
    target: { value: "acme/widgets" },
  })

  expect(
    await screen.findByText(
      "Repository requirements can only make the shared policy stricter."
    )
  ).toBeTruthy()
  const inherited = screen
    .getByText("Inherited shared policy (read-only)")
    .closest("details")
  expect(inherited?.open).toBe(false)
  expect(inherited?.textContent).toContain("Required checks: build")
  expect(inherited?.textContent).toContain("Protected paths: security/**")
  expect(inherited?.textContent).toContain("## Tests")
  expect(inherited?.textContent).toContain("All tests must pass.")
  expect(screen.getByText(/Effective: maximum risk 1/).textContent).toContain(
    "high confidence"
  )
  expect(screen.getByText(/build, security-scan/)).toBeTruthy()
  expect(screen.getByText(/Shadow mode/)).toBeTruthy()
})
