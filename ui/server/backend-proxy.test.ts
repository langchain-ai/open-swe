import { describe, expect, it } from "vitest"

import { dashboardBackendOrigin } from "./backend-proxy"

describe("dashboard backend proxy", () => {
  it("supports a local-only application without a hosted backend", () => {
    expect(dashboardBackendOrigin({})).toBeNull()
  })

  it("normalizes the configured hosted backend origin", () => {
    expect(
      dashboardBackendOrigin({ DASHBOARD_API_URL: "https://example.com/" })
    ).toBe("https://example.com")
  })
})
