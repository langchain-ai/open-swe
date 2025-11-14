import { initApiPassthrough } from "langgraph-nextjs-api-passthrough";
import { encryptSecret } from "@openswe/shared/crypto";
import { SESSION_COOKIE } from "@openswe/shared/constants";

// This file acts as a proxy for requests to your LangGraph server.
// Read the Going to Production section of the documentation for more information.

export const { GET, POST, PUT, PATCH, DELETE, OPTIONS, runtime } =
  initApiPassthrough({
    apiUrl: process.env.LANGGRAPH_API_URL ?? "http://localhost:2024",
    runtime: "nodejs",
    disableWarningLog: true,
    headers: async (req) => {
      const cookieHeader = req.headers.get("cookie");
      if (!cookieHeader) {
        return {};
      }

      const sessionCookie = req.cookies.get(SESSION_COOKIE)?.value;
      if (!sessionCookie) {
        return {};
      }

      const cookies = cookieHeader
        .split(";")
        .map((cookie) => cookie.trim())
        .filter((cookie) => !cookie.startsWith(`${SESSION_COOKIE}=`));

      cookies.push(`${SESSION_COOKIE}=${sessionCookie}`);

      return {
        cookie: cookies.join("; "),
      };
    },
    bodyParameters: (req, body) => {
      if (body.config?.configurable && "apiKeys" in body.config.configurable) {
        const encryptionKey = process.env.SECRETS_ENCRYPTION_KEY;
        if (!encryptionKey) {
          throw new Error(
            "SECRETS_ENCRYPTION_KEY environment variable is required",
          );
        }

        const apiKeys = body.config.configurable.apiKeys;
        const encryptedApiKeys: Record<string, unknown> = {};

        // Encrypt each field in the apiKeys object
        for (const [key, value] of Object.entries(apiKeys)) {
          if (typeof value === "string" && value.trim() !== "") {
            encryptedApiKeys[key] = encryptSecret(value, encryptionKey);
          } else {
            encryptedApiKeys[key] = value;
          }
        }

        // Update the body with encrypted apiKeys
        body.config.configurable.apiKeys = encryptedApiKeys;
        return body;
      }
      return body;
    },
  });
