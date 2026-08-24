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
      sessionReplaySampleRate: 0,
      startSessionReplayRecordingManually: true,
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
        referrer: "https://example.com/login?code=secret",
      },
      resource: {
        type: "fetch",
        url: "https://example.com/attachment?token=secret#preview",
      },
      performance: {
        lcp: {
          resource_url: "https://example.com/image?signature=secret",
        },
      },
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
        url: "https://example.com/agents/thread-id",
        referrer: "https://example.com/login",
      },
      resource: { url: "https://example.com/attachment" },
      performance: {
        lcp: { resource_url: "https://example.com/image" },
      },
      scripts: [{ source_url: "https://example.com/script.js" }],
    })
    expect(getDatadogSessionLink()).toBe(
      "https://app.datadoghq.eu/rum/explorer?query=%40session.id%3Asession%2Fid&tab=session"
    )
  })

  it("uses safe defaults for invalid sample rates", async () => {
    const { rum, load } = rumLoader()

    await initializeDatadogRum(
      {
        VITE_DATADOG_APPLICATION_ID: "app-id",
        VITE_DATADOG_CLIENT_TOKEN: "client-token",
        VITE_DATADOG_SESSION_SAMPLE_RATE: "101",
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
