import { Client } from "@langchain/langgraph-sdk";
import { getGitHubAccessToken } from "@/utils/github";
import { GITHUB_TOKEN_COOKIE } from "@/lib/auth";

export function createClient(apiUrl: string) {
  const githubAccessToken = getGitHubAccessToken();

  return new Client({
    apiUrl,
    defaultHeaders: {
      [GITHUB_TOKEN_COOKIE]: githubAccessToken,
    },
  });
}
