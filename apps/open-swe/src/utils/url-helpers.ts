export const getOpenSweAppUrl = (threadId: string): string => {
  if (!process.env.OPEN_SWE_APP_URL) {
    return "";
  }
  try {
    const baseUrl = new URL(process.env.OPEN_SWE_APP_URL);
    baseUrl.pathname = `/chat/${threadId}`;
    return baseUrl.toString();
  } catch {
    return "";
  }
};
