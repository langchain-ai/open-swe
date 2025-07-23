import { tool } from "@langchain/core/tools";
import { createLogger, LogLevel } from "../utils/logger.js";
import { createSearchDocumentForToolFields } from "@open-swe/shared/open-swe/tools";
import { FireCrawlLoader } from "@langchain/community/document_loaders/web/firecrawl";
import { loadModel, Task } from "../utils/load-model.js";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { getMessageContentString } from "@open-swe/shared/messages";
import { DOCUMENT_SEARCH_PROMPT } from "../constants.js";
import { z } from "zod";

const logger = createLogger(LogLevel.INFO, "SearchDocumentForTool");

type SearchDocumentForInput = z.infer<ReturnType<typeof createSearchDocumentForToolFields>["schema"]>;

export function createSearchDocumentForTool(config: GraphConfig) {
  const searchDocumentForTool = tool(
    async (input: SearchDocumentForInput): Promise<{ result: string; status: "success" | "error" }> => {
      const { url, query } = input;
      
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
        const documentContent = docs.map((doc) => doc.pageContent).join("\n\n");

        if (!documentContent.trim()) {
          return {
            result: `No content found at URL: ${url}`,
            status: "error",
          };
        }

        const model = await loadModel(config, Task.TOC_GENERATION);
        
        const searchPrompt = DOCUMENT_SEARCH_PROMPT
          .replace("{DOCUMENT_PAGE_CONTENT}", documentContent)
          .replace("{NATURAL_LANGUAGE_QUERY}", query);

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
        logger.error("Failed to search document", { url, query, error: errorString });
        return {
          result: `Failed to search document at ${url}\nError:\n${errorString}`,
          status: "error",
        };
      }
    },
    createSearchDocumentForToolFields(),
  );
  
  return searchDocumentForTool;
} 