import { tool } from "@langchain/core/tools";
import { createLogger, LogLevel } from "../utils/logger.js";
import { createGetURLContentToolFields } from "@open-swe/shared/open-swe/tools";
import { FireCrawlLoader } from "@langchain/community/document_loaders/web/firecrawl";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { parseUrl } from "../utils/url-parser.js";

const logger = createLogger(LogLevel.INFO, "GetURLContentTool");

export function createGetURLContentTool(
  state: Pick<GraphState, "documentCache">,
) {
  const getURLContentTool = tool(
    async (
      input,
    ): Promise<{
      result: string;
      status: "success" | "error";
      stateUpdates?: Partial<Pick<GraphState, "documentCache">>;
    }> => {
      const { url } = input;

      const urlParseResult = parseUrl(url);
      if (!urlParseResult.success) {
        return { result: urlParseResult.errorMessage, status: "error" };
      }
      const parsedUrl = urlParseResult.url?.href;

      try {
        let documentContent = state.documentCache[parsedUrl];

        if (!documentContent) {
          logger.info("Document not cached, fetching via FireCrawl", {
            url: parsedUrl,
          });
          const loader = new FireCrawlLoader({
            url: parsedUrl,
            mode: "scrape",
            params: {
              formats: ["markdown"],
            },
          });

          const docs = await loader.load();
          documentContent = docs.map((doc) => doc.pageContent).join("\n\n");

          if (state.documentCache) {
            const stateUpdates = {
              documentCache: {
                ...state.documentCache,
                [parsedUrl]: documentContent,
              },
            };
            return { result: documentContent, status: "success", stateUpdates };
          }
        } else {
          logger.info("Using cached document content", {
            url: parsedUrl,
            contentLength: documentContent.length,
          });
        }

        if (!documentContent.trim()) {
          return {
            result: `No content found at URL: ${url}`,
            status: "error",
          };
        }

        return {
          result: documentContent,
          status: "success",
        };
      } catch (e) {
        const errorString = e instanceof Error ? e.message : String(e);
        logger.error("Failed to get URL content", {
          url: parsedUrl,
          error: errorString,
        });
        return {
          result: `Failed to get URL content: ${parsedUrl}\nError:\n${errorString}`,
          status: "error",
        };
      }
    },
    createGetURLContentToolFields(),
  );
  return getURLContentTool;
}
