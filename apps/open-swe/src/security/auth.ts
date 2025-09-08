import { Auth, HTTPException } from "@langchain/langgraph-sdk/auth";
import { LOCAL_MODE_HEADER } from "@openswe/shared/constants";
import { createWithOwnerMetadata, createOwnerFilter } from "./utils.js";
import { LANGGRAPH_USER_PERMISSIONS } from "../constants.js";
import { validateApiBearerToken } from "./custom.js";

// TODO: Export from LangGraph SDK
export interface BaseAuthReturn {
  is_authenticated?: boolean;
  display_name?: string;
  identity: string;
  permissions: string[];
}

interface AuthenticateReturn extends BaseAuthReturn {
  metadata: {
    installation_name: string;
  };
}

export const auth = new Auth()
  .authenticate<AuthenticateReturn>(async (request: Request) => {
    if (request.method === "OPTIONS") {
      return {
        identity: "anonymous",
        permissions: [],
        is_authenticated: false,
        display_name: "CORS Preflight",
        metadata: {
          installation_name: "n/a",
        },
      };
    }

    // Check for local mode first
    const localModeHeader = request.headers.get(LOCAL_MODE_HEADER);
    const isRunningLocalModeEnv = process.env.OPEN_SWE_LOCAL_MODE === "true";
    if (localModeHeader === "true" && isRunningLocalModeEnv) {
      return {
        identity: "local-user",
        is_authenticated: true,
        display_name: "Local User",
        metadata: {
          installation_name: "local-mode",
        },
        permissions: LANGGRAPH_USER_PERMISSIONS,
      };
    }

    // Bearer token auth (simple API key) — only when header is present
    const authorizationHeader = request.headers.get("authorization");
    if (
      authorizationHeader &&
      authorizationHeader.toLowerCase().startsWith("bearer ")
    ) {
      const token = authorizationHeader.slice(7).trim();
      if (!token) {
        throw new HTTPException(401, { message: "Missing bearer token" });
      }

      const user = validateApiBearerToken(token);
      if (user) {
        return user;
      }
      throw new HTTPException(401, { message: "Invalid API token" });
    }

    throw new HTTPException(401, { message: "Unauthorized" });
  })

  // THREADS: create operations with metadata
  .on("threads:create", ({ value, user }) =>
    createWithOwnerMetadata(value, user),
  )
  .on("threads:create_run", ({ value, user }) =>
    createWithOwnerMetadata(value, user),
  )

  // THREADS: read, update, delete, search operations
  .on("threads:read", ({ user }) => createOwnerFilter(user))
  .on("threads:update", ({ user }) => createOwnerFilter(user))
  .on("threads:delete", ({ user }) => createOwnerFilter(user))
  .on("threads:search", ({ user }) => createOwnerFilter(user))

  // ASSISTANTS: create operation with metadata
  .on("assistants:create", ({ value, user }) =>
    createWithOwnerMetadata(value, user),
  )

  // ASSISTANTS: read, update, delete, search operations
  .on("assistants:read", ({ user }) => createOwnerFilter(user))
  .on("assistants:update", ({ user }) => createOwnerFilter(user))
  .on("assistants:delete", ({ user }) => createOwnerFilter(user))
  .on("assistants:search", ({ user }) => createOwnerFilter(user))

  // STORE: permission-based access
  .on("store", ({ user }) => {
    return { owner: user.identity };
  });
