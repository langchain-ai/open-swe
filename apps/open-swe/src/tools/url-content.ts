import { tool } from "@langchain/core/tools";
import { createLogger, LogLevel } from "../utils/logger.js";
import { createGetURLContentToolFields } from "@open-swe/shared/open-swe/tools";
import { FireCrawlLoader } from "@langchain/community/document_loaders/web/firecrawl";

const logger = createLogger(LogLevel.INFO, "GetURLContentTool");

export function createGetURLContentTool() {
  const getURLContentTool = tool(
    async (input): Promise<{ result: string; status: "success" | "error" }> => {
      const { url } = input;
      let parsedUrl: URL | null = null;
      try {
        parsedUrl = new URL(url);
      } catch (e) {
        const errorString = e instanceof Error ? e.message : String(e);
        logger.error("Failed to parse URL", { url, error: errorString });
        return {
          result: `Failed to parse URL: ${url}\nError:\n${errorString}\nPlease ensure the URL provided is properly formatted.`,
          status: "error",
        };
      }

      try {
        const loader = new FireCrawlLoader({
          url: parsedUrl.href,
          mode: "scrape",
          params: {
            formats: ["markdown"],
          },
        });

        const docs = await loader.load();
        const text = docs.map((doc) => doc.pageContent).join("\n\n");

        return {
          result: text,
          status: "success",
        };
      } catch (e) {
        const errorString = e instanceof Error ? e.message : String(e);
        logger.error("Failed to get URL content", { url, error: errorString });
        return {
          result: `Failed to get URL content: ${url}\nError:\n${errorString}`,
          status: "error",
        };
      }
    },
    createGetURLContentToolFields(),
  );
  return getURLContentTool;
}
