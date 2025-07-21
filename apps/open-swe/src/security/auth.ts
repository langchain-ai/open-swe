
import { Auth, HTTPException } from "@langchain/langgraph-sdk/auth";
import {
  verifyGithubUser,
  GithubUser,
  verifyGithubUserId,
} from "@open-swe/shared/github/verify-user";
import {
  API_KEY_REQUIRED_MESSAGE,
  GITHUB_INSTALLATION_NAME,
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_TOKEN_COOKIE,
  GITHUB_USER_ID_HEADER,
  GITHUB_USER_LOGIN_HEADER,
} from "@open-swe/shared/constants";
import { decryptSecret } from "@open-swe/shared/crypto";
import { verifyGitHubWebhookOrThrow } from "./github.js";
import { createWithOwnerMetadata, createOwnerFilter } from "./utils.js";
import { LANGGRAPH_USER_PERMISSIONS } from "../constants.js";
import { getGitHubPatFromRequest } from "../utils/github-pat.js";
import { isAllowedUser } from "../utils/github/allowed-users.js";
import { validate } from "uuid";

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

function apiKeysInRequestBody(
  bodyStr: string | Record<string, unknown>,
): boolean {
  try {
    const body = typeof bodyStr === "string" ? JSON.parse(bodyStr) : bodyStr;
    if (
      body.config?.configurable &&
      ("anthropicApiKey" in body.config.configurable.apiKeys ||
        "openaiApiKey" in body.config.configurable.apiKeys ||
        "googleApiKey" in body.config.configurable.apiKeys)
    ) {
      return true;
    }
    return false;
  } catch {
    // no-op
    return false;
  }
}

function isRunReq(reqUrl: string): boolean {
  try {
    const url = new URL(reqUrl);
    const pathnameParts = url.pathname.split("/");
    const isCreateAndWait = !!(
      pathnameParts[1] === "threads" &&
      validate(pathnameParts[2]) &&
      pathnameParts[3] === "runs" &&
      pathnameParts[4] === "wait" &&
      pathnameParts.length === 5
    );
    const isCreateBackground = !!(
      pathnameParts[1] === "threads" &&
      validate(pathnameParts[2]) &&
      pathnameParts[3] === "runs" &&
      pathnameParts.length === 4
    );
    const isCreateStream = !!(
      pathnameParts[1] === "threads" &&
      validate(pathnameParts[2]) &&
      pathnameParts[3] === "runs" &&
      pathnameParts[4] === "stream" &&
      pathnameParts.length === 5
    );

    return !!isCreateAndWait || !!isCreateBackground || !!isCreateStream;
  } catch {
    // no-op
    return false;
  }
}

export const auth = new Auth()
  .authenticate<AuthenticateReturn>(async (request: Request) => {
    const isProdEnv = process.env.NODE_ENV === "production";
    // Disable all requests to the server in prod for now.
    if (!isProdEnv) {
      return new HTTPException(504, {
        message: "Open SWE temporarily disabled.",
      }) as any;
    }

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

    const isProd = process.env.NODE_ENV === "production";

    const ghSecretHashHeader = request.headers.get("X-Hub-Signature-256");
    if (ghSecretHashHeader) {
      // This will either return a valid user, or throw an error
      return await verifyGitHubWebhookOrThrow(request);
    }

    const encryptionKey = process.env.SECRETS_ENCRYPTION_KEY;
    if (!encryptionKey) {
      throw new Error("Missing SECRETS_ENCRYPTION_KEY environment variable.");
    }

    // Check for GitHub PAT authentication (simpler mode for evals, etc.)
    const githubPat = getGitHubPatFromRequest(request, encryptionKey);
    if (githubPat && !isProd) {
      const user = await verifyGithubUser(githubPat);
      if (!user) {
        throw new HTTPException(401, {
          message: "Invalid GitHub PAT",
        });
      }

      return {
        identity: user.id.toString(),
        is_authenticated: true,
        display_name: user.login,
        metadata: {
          installation_name: "pat-auth",
        },
        permissions: LANGGRAPH_USER_PERMISSIONS,
      };
    }

    // GitHub App authentication mode (existing logic)
    const installationNameHeader = request.headers.get(
      GITHUB_INSTALLATION_NAME,
    );
    if (!installationNameHeader) {
      throw new HTTPException(401, {
        message: "GitHub installation name header missing",
      });
    }

    // We don't do anything with this token right now, but still confirm it
    // exists as it will cause issues later on if it's not present.
    const encryptedInstallationToken = request.headers.get(
      GITHUB_INSTALLATION_TOKEN_COOKIE,
    );
    if (!encryptedInstallationToken) {
      throw new HTTPException(401, {
        message: "GitHub installation token header missing",
      });
    }

    const encryptedAccessToken = request.headers.get(GITHUB_TOKEN_COOKIE);
    const decryptedAccessToken = encryptedAccessToken
      ? decryptSecret(encryptedAccessToken, encryptionKey)
      : undefined;
    const decryptedInstallationToken = decryptSecret(
      encryptedInstallationToken,
      encryptionKey,
    );

    let user: GithubUser | undefined;

    if (!decryptedAccessToken) {
      // If there isn't a user access token, check to see if the user info is in headers.
      // This would indicate a bot created the request.
      const userIdHeader = request.headers.get(GITHUB_USER_ID_HEADER);
      const userLoginHeader = request.headers.get(GITHUB_USER_LOGIN_HEADER);
      if (!userIdHeader || !userLoginHeader) {
        throw new HTTPException(401, {
          message: "Github-User-Id or Github-User-Login header missing",
        });
      }
      user = await verifyGithubUserId(
        decryptedInstallationToken,
        Number(userIdHeader),
        userLoginHeader,
      );
    } else {
      // Ensure we decrypt the token before passing to the verification function.
      user = await verifyGithubUser(decryptedAccessToken);
    }

    if (!user) {
      throw new HTTPException(401, {
        message: "User not found",
      });
    }

    const reqCopy = request.clone();
    const reqBody = await reqCopy.text();
    if (!isAllowedUser(user.login)) {
      if (isRunReq(request.url)) {
        if (!apiKeysInRequestBody(reqBody)) {
          throw new HTTPException(401, {
            message: API_KEY_REQUIRED_MESSAGE,
          });
        }
      }
    }

    return {
      identity: user.id.toString(),
      is_authenticated: true,
      display_name: user.login,
      metadata: {
        installation_name: installationNameHeader,
      },
      permissions: LANGGRAPH_USER_PERMISSIONS,
    };
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
