import { Auth, HTTPException } from "@langchain/langgraph-sdk/auth";
import {
  verifyGithubUser,
  GithubUser,
} from "@open-swe/shared/github/verify-user";
import {
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_TOKEN_COOKIE,
} from "@open-swe/shared/constants";
import { decryptGitHubToken } from "@open-swe/shared/crypto";
import { verifyGitHubWebhookOrThrow } from "./github.js";
import { createWithOwnerMetadata, createOwnerFilter } from "./utils.js";

export const auth = new Auth()
  .authenticate(async (request: Request) => {
    if (request.method === "OPTIONS") {
      return {
        identity: "anonymous",
        permissions: [],
        is_authenticated: false,
        display_name: "CORS Preflight",
      };
    }

    const ghSecretHashHeader = request.headers.get("X-Hub-Signature-256");
    if (ghSecretHashHeader) {
      // This will either return a valid user, or throw an error
      return await verifyGitHubWebhookOrThrow(request);
    }

    const encryptionKey = process.env.GITHUB_TOKEN_ENCRYPTION_KEY;
    if (!encryptionKey) {
      throw new Error(
        "Missing GITHUB_TOKEN_ENCRYPTION_KEY environment variable.",
      );
    }

    // Parse Authorization header
    const encryptedAccessToken = request.headers.get(GITHUB_TOKEN_COOKIE);
    if (!encryptedAccessToken) {
      throw new HTTPException(401, {
        message: "GitHub access token header missing",
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

    // Validate GitHub access token
    let user: GithubUser | undefined;
    try {
      // Ensure we decrypt the token before passing to the verification function.
      user = await verifyGithubUser(
        decryptGitHubToken(encryptedAccessToken, encryptionKey),
      );
      if (!user) {
        throw new HTTPException(401, {
          message:
            "Invalid GitHub token or user is not a member of the required organization.",
        });
      }
    } catch (e: any) {
      throw new HTTPException(401, {
        message: `Authentication error: ${e.message}`,
      });
    }
    return {
      identity: user.id.toString(),
      is_authenticated: true,
      display_name: user.login,
      permissions: [
        "threads:create",
        "threads:create_run",
        "threads:read",
        "threads:delete",
        "threads:update",
        "threads:search",
        "assistants:create",
        "assistants:read",
        "assistants:delete",
        "assistants:update",
        "assistants:search",
        "deployments:read",
        "deployments:search",
        "store:access",
      ],
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
