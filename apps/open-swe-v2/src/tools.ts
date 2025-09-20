import { tool } from "@langchain/core/tools";
import { z } from "zod";
import { spawn } from "child_process";
import { validateCommandSafety } from "./command-safety.js";

// Execute bash command tool
export const executeBash = tool(
  async ({
    command,
    timeout = 30000,
  }: {
    command: string;
    timeout?: number;
  }) => {
    try {
      // First, validate command safety (focusing on prompt injection)
      const safetyValidation = await validateCommandSafety(command);

      // If command is not safe, return error without executing
      if (!safetyValidation.is_safe) {
        return {
          success: false,
          returncode: -1,
          stdout: "",
          stderr: `Command blocked - safety validation failed:\nThreat Type: ${safetyValidation.threat_type}\nReasoning: ${safetyValidation.reasoning}\nDetected Patterns: ${safetyValidation.detected_patterns.join(", ")}`,
          safety_validation: safetyValidation,
        };
      }

      return new Promise((resolve) => {
        const child = spawn("bash", ["-c", command], {
          stdio: ["pipe", "pipe", "pipe"],
        });

        let stdout = "";
        let stderr = "";

        child.stdout.on("data", (data) => {
          stdout += data.toString();
        });

        child.stderr.on("data", (data) => {
          stderr += data.toString();
        });

        const timeoutId = setTimeout(() => {
          child.kill();
          resolve({
            success: false,
            returncode: -1,
            stdout,
            stderr: stderr + "\nProcess timed out",
            safety_validation: safetyValidation,
          });
        }, timeout);

        child.on("close", (code) => {
          clearTimeout(timeoutId);
          resolve({
            success: code === 0,
            returncode: code || 0,
            stdout,
            stderr,
            safety_validation: safetyValidation,
          });
        });

        child.on("error", (err) => {
          clearTimeout(timeoutId);
          resolve({
            success: false,
            returncode: -1,
            stdout,
            stderr: err.message,
            safety_validation: safetyValidation,
          });
        });
      });
    } catch (error) {
      return {
        success: false,
        returncode: -1,
        stdout: "",
        stderr: `Error executing command: ${error instanceof Error ? error.message : String(error)}`,
      };
    }
  },
  {
    name: "execute_bash",
    description: "Execute a bash command and return the result",
    schema: z.object({
      command: z.string().describe("The bash command to execute"),
      timeout: z
        .number()
        .optional()
        .default(30000)
        .describe("Timeout in milliseconds"),
    }),
  },
);

// HTTP request tool
export const httpRequest = tool(
  async ({
    url,
    method = "GET",
    headers = {},
    data,
  }: {
    url: string;
    method?: string;
    headers?: Record<string, string>;
    data?: any;
  }) => {
    try {
      const fetchOptions: RequestInit = {
        method,
        headers: {
          "Content-Type": "application/json",
          ...headers,
        },
      };

      if (data && method !== "GET") {
        fetchOptions.body = JSON.stringify(data);
      }

      const response = await fetch(url, fetchOptions);
      const responseData = await response.text();

      // Convert headers to plain object
      const headersObj: Record<string, string> = {};
      response.headers.forEach((value, key) => {
        headersObj[key] = value;
      });

      return {
        status: response.status,
        headers: headersObj,
        data: responseData,
      };
    } catch (error) {
      return {
        error: error instanceof Error ? error.message : String(error),
      };
    }
  },
  {
    name: "http_request",
    description: "Make an HTTP request to a URL",
    schema: z.object({
      url: z.string().describe("The URL to make the request to"),
      method: z.string().optional().default("GET").describe("HTTP method"),
      headers: z
        .record(z.string())
        .optional()
        .default({})
        .describe("HTTP headers"),
      data: z.any().optional().describe("Request body data"),
    }),
  },
);

// Web search tool (Tavily implementation)
export const webSearch = tool(
  async ({ query, maxResults = 5 }: { query: string; maxResults?: number }) => {
    const apiKey = process.env.TAVILY_API_KEY;

    if (!apiKey) {
      throw new Error("TAVILY_API_KEY environment variable is not set");
    }

    try {
      const response = await fetch("https://api.tavily.com/search", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          api_key: apiKey,
          query: query,
          max_results: maxResults,
          search_depth: "basic",
          include_answer: true,
          include_images: false,
          include_raw_content: false,
          format_output: true,
        }),
      });

      if (!response.ok) {
        throw new Error(
          `Tavily API error: ${response.status} ${response.statusText}`,
        );
      }

      const data = (await response.json()) as any;

      return {
        answer: data.answer || null,
        results:
          data.results?.map((result: any) => ({
            title: result.title,
            url: result.url,
            content: result.content,
            score: result.score,
            published_date: result.published_date,
          })) || [],
        query: data.query || query,
      };
    } catch {
      return {
        answer: null,
        results: [
          {
            title: `Search result for: ${query}`,
            url: `https://example.com/search?q=${encodeURIComponent(query)}`,
            content: `This is a fallback mock search result for the query: ${query}`,
            score: 0.5,
            published_date: new Date().toISOString(),
          },
        ],
        query,
        response_time: 0,
      };
    }
  },
  {
    name: "web_search",
    description: "Search the web for information using Tavily API",
    schema: z.object({
      query: z.string().describe("The search query"),
      maxResults: z
        .number()
        .optional()
        .default(5)
        .describe("Maximum number of results to return"),
    }),
  },
);
