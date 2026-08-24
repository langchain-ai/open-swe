import { LangGraphSettings } from "@t3tools/contracts";
import { describe, expect, it } from "@effect/vitest";
import * as Schema from "effect/Schema";

import { langGraphAuthHeaders } from "./LangGraphProvider.ts";

const decodeLangGraphSettings = Schema.decodeSync(LangGraphSettings);

describe("langGraphAuthHeaders", () => {
  it("uses the configured key before the local environment token", () => {
    expect(
      langGraphAuthHeaders(decodeLangGraphSettings({ apiKey: " configured " }), {
        OPEN_SWE_LOCAL_AUTH_TOKEN: "environment",
      }),
    ).toEqual({
      "x-api-key": "configured",
      authorization: "Bearer configured",
    });
  });

  it("uses the local environment token when no key is configured", () => {
    expect(
      langGraphAuthHeaders(decodeLangGraphSettings({ apiKey: "" }), {
        OPEN_SWE_LOCAL_AUTH_TOKEN: " local-token ",
      }),
    ).toEqual({
      "x-api-key": "local-token",
      authorization: "Bearer local-token",
    });
  });

  it("omits authentication when neither source contains a key", () => {
    expect(
      langGraphAuthHeaders(decodeLangGraphSettings({ apiKey: "  " }), {
        OPEN_SWE_LOCAL_AUTH_TOKEN: "  ",
      }),
    ).toEqual({});
  });
});
