import { marked } from "marked";
import TurndownService from "turndown";

const turndown = new TurndownService({
  headingStyle: "atx",
  codeBlockStyle: "fenced",
  bulletListMarker: "-",
});

function markdownToHtml(markdown: string): string {
  const source = markdown.trim();
  if (source.length === 0) return "";
  return marked.parse(source, { async: false });
}

function htmlToMarkdown(html: string): string {
  return turndown.turndown(html).trim();
}

export { htmlToMarkdown, markdownToHtml };
