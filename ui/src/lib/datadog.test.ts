import { describe, expect, it, vi } from "vitest"

import { getDatadogSessionLink, initializeDatadogRum } from "./datadog"
import type { RumInitConfiguration } from "@datadog/browser-rum"

function rumClient(sessionId?: string) {
  return {
    init: vi.fn<(configuration: RumInitConfiguration) => void>(),
    getInternalContext: vi.fn(() =>
      sessionId ? { session_id: sessionId } : undefined
    ),
  }
}

function rumLoader(rum = rumClient()) {
  return { rum, load: vi.fn(async () => rum) }
}

describe("initializeDatadogRum", () => {
  it("does nothing without both public identifiers", () => {
    const { load } = rumLoader()

    void initializeDatadogRum({ VITE_DATADOG_APPLICATION_ID: "app-id" }, load)

    expect(load).not.toHaveBeenCalled()
  })

  it("initializes RUM from public environment settings", async () => {
    const { rum, load } = rumLoader(rumClient("session/id"))

    await initializeDatadogRum(
      {
        MODE: "staging",
        VITE_DATADOG_APPLICATION_ID: " app-id ",
        VITE_DATADOG_CLIENT_TOKEN: " client-token ",
        VITE_DATADOG_SITE: "datadoghq.eu",
        VITE_DATADOG_SERVICE: "dashboard",
        VITE_DATADOG_VERSION: "1.2.3",
        VITE_DATADOG_SESSION_SAMPLE_RATE: "25",
        VITE_DATADOG_SESSION_REPLAY_SAMPLE_RATE: "50",
      },
      load
    )

    expect(rum.init).toHaveBeenCalledWith({
      applicationId: "app-id",
      clientToken: "client-token",
      site: "datadoghq.eu",
      service: "dashboard",
      env: "staging",
      version: "1.2.3",
      sessionSampleRate: 25,
      sessionReplaySampleRate: 50,
      trackUserInteractions: true,
      trackResources: true,
      trackLongTasks: true,
      beforeSend: expect.any(Function),
      defaultPrivacyLevel: "mask",
      enablePrivacyForActionName: true,
    })

    const configuration = rum.init.mock.calls[0]?.[0]
    const resourceEvent = {
      type: "resource",
      view: {
        id: "view-id",
        url: "https://example.com/agents/thread-id?access=secret#message",
        referrer:
          "https://example.com/agents/reviews/langchain-ai/open-swe/2203?tab=files",
      },
      resource: {
        type: "fetch",
        url: "https://example.com/langchain-ai/open-swe/pull/2203?token=secret#preview",
      },
      performance: {
        lcp: {
          resource_url: "https://cdn.example.com/image?signature=secret",
        },
      },
      routes: [
        { url: "/agents/thread-id/plan" },
        { url: "/agents/automations/schedule-id" },
        { url: "/review/repositories/langchain-ai" },
        { url: "/agents/environments" },
        { url: "/agents/instructions" },
        { url: "/agents/snapshots" },
      ],
      urlClasses: [
        { url: "https://github.com/langchain-ai/open-swe/pull/2203?tab=files" },
        { url: "//example.com/agents/another-thread?secret=value" },
        { url: "assets/image.png?secret=value" },
        { url: "blob:https://example.com/id?secret=value" },
        { url: "data:image/png;base64,abc?secret=value" },
        { url: "not a url?secret=value" },
      ],
      scripts: [
        { source_url: "https://example.com/script.js?cache=secret#module" },
      ],
    }
    configuration?.beforeSend?.(
      resourceEvent as unknown as Parameters<
        NonNullable<RumInitConfiguration["beforeSend"]>
      >[0],
      {} as Parameters<NonNullable<RumInitConfiguration["beforeSend"]>>[1]
    )
    expect(resourceEvent).toMatchObject({
      view: {
        url: "https://example.com/agents/:threadId",
        referrer: "https://example.com/agents/reviews/:owner/:repo/:number",
      },
      resource: { url: "https://example.com/:owner/:repo/pull/:number" },
      performance: {
        lcp: { resource_url: "https://cdn.example.com/image" },
      },
      routes: [
        { url: "/agents/:threadId/plan" },
        { url: "/agents/automations/:scheduleId" },
        { url: "/review/repositories/:owner" },
        { url: "/agents/environments" },
        { url: "/agents/instructions" },
        { url: "/agents/snapshots" },
      ],
      urlClasses: [
        { url: "https://github.com/langchain-ai/open-swe/pull/2203" },
        { url: "//example.com/agents/:threadId" },
        { url: "assets/image.png" },
        { url: "blob:https://example.com/id" },
        { url: "data:image/png;base64,abc" },
        { url: "not a url" },
      ],
      scripts: [{ source_url: "https://example.com/script.js" }],
    })
    expect(getDatadogSessionLink()).toBe(
      "https://app.datadoghq.eu/rum/explorer?query=%40session.id%3Asession%2Fid&tab=session"
    )
  })

  it("enables replay by default", async () => {
    const { rum, load } = rumLoader()

    await initializeDatadogRum(
      {
        VITE_DATADOG_APPLICATION_ID: "app-id",
        VITE_DATADOG_CLIENT_TOKEN: "client-token",
      },
      load
    )

    expect(rum.init).toHaveBeenCalledWith(
      expect.objectContaining({ sessionReplaySampleRate: 100 })
    )
  })

  it("uses safe defaults for invalid sample rates", async () => {
    const { rum, load } = rumLoader()

    await initializeDatadogRum(
      {
        VITE_DATADOG_APPLICATION_ID: "app-id",
        VITE_DATADOG_CLIENT_TOKEN: "client-token",
        VITE_DATADOG_SESSION_SAMPLE_RATE: "101",
        VITE_DATADOG_SESSION_REPLAY_SAMPLE_RATE: " ",
      },
      load
    )

    expect(rum.init).toHaveBeenCalledWith(
      expect.objectContaining({
        sessionSampleRate: 100,
        sessionReplaySampleRate: 0,
      })
    )
  })
})
