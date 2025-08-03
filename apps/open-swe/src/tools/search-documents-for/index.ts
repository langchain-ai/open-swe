import { tool } from "@langchain/core/tools";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { createSearchDocumentForToolFields } from "@open-swe/shared/open-swe/tools";
import { FireCrawlLoader } from "@langchain/community/document_loaders/web/firecrawl";
import { loadModel } from "../../utils/llms/index.js";
import { LLMTask } from "@open-swe/shared/open-swe/llm-task";
import { GraphConfig, GraphState } from "@open-swe/shared/open-swe/types";
import { getMessageContentString } from "@open-swe/shared/messages";
import { DOCUMENT_SEARCH_PROMPT } from "./prompt.js";
import { parseUrl } from "../../utils/url-parser.js";
import { z } from "zod";

const logger = createLogger(LogLevel.INFO, "SearchDocumentForTool");

type SearchDocumentForInput = z.infer<
  ReturnType<typeof createSearchDocumentForToolFields>["schema"]
>;

export function createSearchDocumentForTool(
  state: Pick<GraphState, "documentCache">,
  config: GraphConfig,
) {
  const searchDocumentForTool = tool(
    async (
      input: SearchDocumentForInput,
    ): Promise<{
      result: string;
      status: "success" | "error";
      stateUpdates?: Partial<Pick<GraphState, "documentCache">>;
    }> => {
      const { url, query } = input;

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

        const model = await loadModel(config, LLMTask.SUMMARIZER);

        const searchPrompt = DOCUMENT_SEARCH_PROMPT.replace(
          "{DOCUMENT_PAGE_CONTENT}",
          documentContent,
        ).replace("{NATURAL_LANGUAGE_QUERY}", query);

        const response = await model
          .withConfig({ tags: ["nostream"], runName: "document-search" })
          .invoke([
            {
              role: "user",
              content: searchPrompt,
            },
          ]);

        const searchResult = getMessageContentString(response.content);

        logger.info("Document search completed", {
          url,
          query,
          resultLength: searchResult.length,
        });

        return {
          result: searchResult,
          status: "success",
        };
      } catch (e) {
        const errorString = e instanceof Error ? e.message : String(e);
        logger.error("Failed to search document", {
          url: parsedUrl,
          query,
          error: errorString,
        });
        return {
          result: `Failed to search document at ${parsedUrl}\nError:\n${errorString}`,
          status: "error",
        };
      }
    },
    createSearchDocumentForToolFields(),
  );

  return searchDocumentForTool;
}
