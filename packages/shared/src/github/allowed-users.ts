// HACK: Until we setup proper support for API credits, we will only allow users to self host Open SWE
export function isAllowedUser(username: string): boolean {
  const nodeEnv = process.env.NODE_ENV;
  // Allow all users in non-production environments
  if (nodeEnv !== "production") {
    return true;
  }

  const restrictToLangChainAuth =
    process.env.RESTRICT_TO_LANGCHAIN_AUTH === "true" ||
    process.env.NEXT_PUBLIC_RESTRICT_TO_LANGCHAIN_AUTH === "true";
  if (!restrictToLangChainAuth) {
    return true;
  }

  let allowedUsers: string[] = [];
  try {
    allowedUsers = process.env.NEXT_PUBLIC_ALLOWED_USERS_LIST
      ? JSON.parse(process.env.NEXT_PUBLIC_ALLOWED_USERS_LIST)
      : [];
    if (!allowedUsers.length) {
      return false;
    }
  } catch (error) {
    // eslint-disable-next-line no-console
    console.error("Failed to parse allowed users list", error);
    return false;
  }

  return allowedUsers.some((u) => u === username);
}
