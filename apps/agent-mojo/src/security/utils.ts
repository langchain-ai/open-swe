export const STUDIO_USER_ID = "langgraph-studio-user";

// Helper function to check if user is studio user
export function isStudioUser(userIdentity: string): boolean {
  return userIdentity === STUDIO_USER_ID;
}

// Helper function for operations that only need owner filtering
export function createOwnerFilter(user: { identity: string }) {
  if (isStudioUser(user.identity)) {
    return;
  }
  return { owner: user.identity };
}

// Helper function for create operations that set metadata
export function createWithOwnerMetadata(
  value: any,
  user: { identity: string; metadata: { installation_name: string } },
) {
  if (isStudioUser(user.identity)) {
    return;
  }

  value.metadata ??= {};
  value.metadata.owner = user.identity;
  value.metadata.installation_name = user.metadata.installation_name;
  return { owner: user.identity };
}
