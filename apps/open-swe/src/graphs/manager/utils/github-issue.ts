const DEFAULT_ISSUE_TITLE = "New Open SWE Request";
const ISSUE_TITLE_OPEN_TAG = "<issue-title>";
const ISSUE_TITLE_CLOSE_TAG = "</issue-title>";
const ISSUE_CONTENT_OPEN_TAG = "<issue-content>";
const ISSUE_CONTENT_CLOSE_TAG = "</issue-content>";

export function extractIssueTitleAndContentFromMessage(content: string) {
  let messageTitle = DEFAULT_ISSUE_TITLE;
  let messageContent = content;
  if (
    content.includes(ISSUE_TITLE_OPEN_TAG) &&
    content.includes(ISSUE_TITLE_CLOSE_TAG)
  ) {
    messageTitle = content.substring(
      content.indexOf(ISSUE_TITLE_OPEN_TAG) + ISSUE_TITLE_OPEN_TAG.length,
      content.indexOf(ISSUE_TITLE_CLOSE_TAG),
    );
  }
  if (
    content.includes(ISSUE_CONTENT_OPEN_TAG) &&
    content.includes(ISSUE_CONTENT_CLOSE_TAG)
  ) {
    messageContent = content.substring(
      content.indexOf(ISSUE_CONTENT_OPEN_TAG) + ISSUE_CONTENT_OPEN_TAG.length,
      content.indexOf(ISSUE_CONTENT_CLOSE_TAG),
    );
  }
  return { title: messageTitle, content: messageContent };
}
