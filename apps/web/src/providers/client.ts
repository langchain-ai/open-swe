import { Client } from "@langchain/langgraph-sdk";

export function createClient(apiUrl: string) {
  // const githubAccessToken = getGitHubAccessToken();

  return new Client({
    apiUrl,
    // defaultHeaders: {
    //   [GITHUB_TOKEN_COOKIE]: githubAccessToken,
    // },
  });
}
