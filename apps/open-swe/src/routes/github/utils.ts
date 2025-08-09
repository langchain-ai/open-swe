export function createDevMetadataComment(runId: string, threadId: string) {
  return `<details>
  <summary>Dev Metadata</summary>
  ${JSON.stringify(
    {
      runId,
      threadId,
    },
    null,
    2,
  )}
</details>`;
}

export function mentionsOpenSWE(commentBody: string): boolean {
  return /@open-swe\b/.test(commentBody);
}

export function extractLinkedIssues(prBody: string): number[] {
  // Look for common patterns like "fixes #123", "closes #456", "resolves #789"
  const patterns = [
    /(?:fixes?|closes?|resolves?)\s+#(\d+)/gi,
    /(?:fix|close|resolve)\s+#(\d+)/gi,
  ];

  const issueNumbers: number[] = [];
  patterns.forEach((pattern) => {
    let match;
    while ((match = pattern.exec(prBody)) !== null) {
      issueNumbers.push(parseInt(match[1], 10));
    }
  });

  return [...new Set(issueNumbers)]; // Remove duplicates
}
