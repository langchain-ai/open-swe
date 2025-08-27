import { tool } from "@langchain/core/tools";
import { z } from "zod";
import { spawn } from "child_process";
import { validateCommandSafety } from "./command-safety.js";
import { getConfigValue } from "@open-swe/shared";
import * as fs from "fs";
import * as path from "path";
import { Command } from "@langchain/langgraph";
import { ToolMessage } from "@langchain/core/messages";

// Execute bash command tool
export const executeBash = tool(
  async (
    {
      command,
      timeout = 30000,
    }: {
      command: string;
      timeout?: number;
    },
    config,
  ) => {
    try {
      const currentTaskInput =
        config?.configurable?.__pregel_scratchpad?.currentTaskInput;
      const workingDirectory =
        currentTaskInput?.working_directory || process.cwd();

      const safetyValidation = await validateCommandSafety(command);

      if (!safetyValidation.is_safe) {
        return `Command blocked - safety validation failed:\nThreat Type: ${safetyValidation.threat_type}\nReasoning: ${safetyValidation.reasoning}${safetyValidation.detected_patterns.length > 0 ? `\nDetected Patterns: ${safetyValidation.detected_patterns.join(", ")}` : ""}`;
      }

      return new Promise((resolve) => {
        const child = spawn("bash", ["-c", command], {
          stdio: ["pipe", "pipe", "pipe"],
          cwd: workingDirectory,
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
          resolve(
            `Command timed out after ${timeout}ms${stdout ? `\nOutput: ${stdout}` : ""}${stderr ? `\nError: ${stderr}` : ""}`,
          );
        }, timeout);

        child.on("close", (code) => {
          clearTimeout(timeoutId);
          if (code === 0) {
            if (!stdout.trim() && !stderr.trim()) {
              resolve("Command executed successfully");
            } else {
              resolve(stdout + (stderr ? `\nSTDERR: ${stderr}` : ""));
            }
          } else {
            resolve(
              `Command failed with exit code ${code || 0}${stdout ? `\nOutput: ${stdout}` : ""}${stderr ? `\nError: ${stderr}` : ""}`,
            );
          }
        });

        child.on("error", (err) => {
          clearTimeout(timeoutId);
          resolve(
            `Error executing command: ${err.message}${stdout ? `\nOutput: ${stdout}` : ""}${stderr ? `\nError output: ${stderr}` : ""}`,
          );
        });
      });
    } catch (error) {
      return `Error executing command: ${error instanceof Error ? error.message : String(error)}`;
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

      let responseData = await response.text();

      if (responseData.length > 10000) {
        responseData =
          responseData.substring(0, 10000) +
          "\n... (content truncated due to size)";
      }

      const headersObj: Record<string, string> = {};
      response.headers.forEach((value, key) => {
        headersObj[key] = value;
      });

      if (response.ok) {
        return `HTTP request successful (${response.status}). Response size: ${responseData.length} characters.`;
      } else {
        return `HTTP ${response.status}: Request failed`;
      }
    } catch (error) {
      const errorMessage = `HTTP Error: ${error instanceof Error ? error.message : String(error)}`;

      return errorMessage;
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
    const apiKey = getConfigValue("TAVILY_API_KEY");

    if (!apiKey) {
      throw new Error(
        "TAVILY_API_KEY not found in config or environment variables",
      );
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

// Custom ls tool that respects working_directory from state
export const ls = tool(
  (
    {
      path: dirPath = ".",
    }: {
      path?: string;
    },
    config,
  ) => {
    try {
      const currentTaskInput =
        config?.configurable?.__pregel_scratchpad?.currentTaskInput;
      const workingDirectory =
        currentTaskInput?.working_directory || process.cwd();

      const resolvedPath = path.resolve(workingDirectory, dirPath);

      if (!fs.existsSync(resolvedPath)) {
        return [`Error: Path '${resolvedPath}' does not exist`];
      }

      const stat = fs.statSync(resolvedPath);
      if (!stat.isDirectory()) {
        return [`Error: Path '${resolvedPath}' is not a directory`];
      }

      const items = fs.readdirSync(resolvedPath);
      return items.sort();
    } catch (e) {
      return [`Error listing directory: ${String(e)}`];
    }
  },
  {
    name: "ls",
    description: "List files and directories in the local filesystem",
    schema: z.object({
      path: z
        .string()
        .optional()
        .default(".")
        .describe("Directory path to list (relative to working directory)"),
    }),
  },
);

// Custom read_file tool that respects working_directory from state
export const readFile = tool(
  (
    {
      file_path,
      offset = 0,
      limit = 500,
    }: {
      file_path: string;
      offset?: number;
      limit?: number;
    },
    config,
  ) => {
    try {
      const currentTaskInput =
        config?.configurable?.__pregel_scratchpad?.currentTaskInput;
      const workingDirectory =
        currentTaskInput?.working_directory || process.cwd();

      const resolvedPath = path.resolve(workingDirectory, file_path);

      if (!fs.existsSync(resolvedPath)) {
        return `Error: File '${resolvedPath}' not found`;
      }

      const stat = fs.statSync(resolvedPath);
      if (!stat.isFile()) {
        return `Error: '${resolvedPath}' is not a file`;
      }

      let content: string;
      try {
        content = fs.readFileSync(resolvedPath, "utf-8");
      } catch {
        // Try reading with error handling for non-UTF8 content
        const buffer = fs.readFileSync(resolvedPath);
        content = buffer.toString("utf-8", 0, buffer.length);
      }

      if (!content || content.trim() === "") {
        return "System reminder: File exists but has empty contents";
      }

      const lines = content.split("\n");
      const startIdx = offset;
      const endIdx = Math.min(startIdx + limit, lines.length);

      if (startIdx >= lines.length) {
        return `Error: Line offset ${offset} exceeds file length (${lines.length} lines)`;
      }

      const resultLines: string[] = [];
      for (let i = startIdx; i < endIdx; i++) {
        let lineContent = lines[i];

        if (lineContent.length > 2000) {
          lineContent = lineContent.substring(0, 2000);
        }

        const lineNumber = i + 1;
        resultLines.push(
          `${lineNumber.toString().padStart(6)}\t${lineContent}`,
        );
      }

      return resultLines.join("\n");
    } catch (e) {
      return `Error reading file: ${String(e)}`;
    }
  },
  {
    name: "read_file",
    description: "Read a file from the local filesystem",
    schema: z.object({
      file_path: z
        .string()
        .describe("Path to the file to read (relative to working directory)"),
      offset: z
        .number()
        .optional()
        .default(0)
        .describe("Line offset to start reading from"),
      limit: z
        .number()
        .optional()
        .default(2000)
        .describe("Maximum number of lines to read"),
    }),
  },
);

// Custom write_file tool that respects working_directory from state
export const writeFile = tool(
  (
    {
      file_path,
      content,
    }: {
      file_path: string;
      content: string;
    },
    config,
  ) => {
    try {
      // Get working directory from state
      const currentTaskInput =
        config?.configurable?.__pregel_scratchpad?.currentTaskInput;
      const workingDirectory =
        currentTaskInput?.working_directory || process.cwd();

      // Resolve file_path relative to working directory
      const resolvedPath = path.resolve(workingDirectory, file_path);

      const dir = path.dirname(resolvedPath);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }

      fs.writeFileSync(resolvedPath, content, "utf-8");
      return `Successfully wrote to file '${resolvedPath}'`;
    } catch (e) {
      return `Error writing file: ${String(e)}`;
    }
  },
  {
    name: "write_file",
    description: "Write content to a file in the local filesystem",
    schema: z.object({
      file_path: z
        .string()
        .describe("Path to the file to write (relative to working directory)"),
      content: z.string().describe("Content to write to the file"),
    }),
  },
);

// Custom str_replace_based_edit_tool that respects working_directory from state
export const strReplaceBasedEditTool = tool(
  (
    {
      command,
      path: filePath,
      view_range,
      old_str,
      new_str,
      file_text,
      insert_line,
    }: {
      command: "view" | "str_replace" | "create" | "insert";
      path: string;
      view_range?: [number, number];
      old_str?: string;
      new_str?: string;
      file_text?: string;
      insert_line?: number;
    },
    config,
  ) => {
    try {
      // Get working directory from state
      const currentTaskInput =
        config?.configurable?.__pregel_scratchpad?.currentTaskInput;
      const workingDirectory =
        currentTaskInput?.working_directory || process.cwd();

      // Resolve filePath relative to working directory
      const resolvedPath = path.resolve(workingDirectory, filePath);

      if (command === "view") {
        if (fs.existsSync(resolvedPath)) {
          const stat = fs.statSync(resolvedPath);

          if (stat.isDirectory()) {
            try {
              const items = fs.readdirSync(resolvedPath);
              const sortedItems = items
                .map((item) => {
                  const itemPath = path.join(resolvedPath, item);
                  const itemStat = fs.statSync(itemPath);
                  return itemStat.isDirectory() ? `${item}/` : item;
                })
                .sort();
              return sortedItems.join("\n");
            } catch (e) {
              return `Error listing directory: ${String(e)}`;
            }
          } else if (stat.isFile()) {
            try {
              const content = fs.readFileSync(resolvedPath, "utf-8");
              const lines = content.split("\n");

              let selectedLines = lines;
              let startNum = 1;

              if (view_range) {
                const [startLine, endLine] = view_range;
                const startIdx = Math.max(0, startLine - 1);
                const endIdx = Math.min(lines.length, endLine);
                selectedLines = lines.slice(startIdx, endIdx);
                startNum = startLine;
              }

              const resultLines = selectedLines.map((line, i) => {
                const lineNum = startNum + i;
                return `${lineNum.toString().padStart(4)} | ${line}`;
              });

              return resultLines.length > 0
                ? resultLines.join("\n")
                : "File is empty";
            } catch {
              return `Error: File contains non-UTF-8 content`;
            }
          }
        }
        return `Error: Path '${filePath}' does not exist`;
      } else if (command === "str_replace") {
        if (!old_str || new_str === undefined) {
          return "Error: str_replace requires both old_str and new_str parameters";
        }

        if (!fs.existsSync(resolvedPath)) {
          return `Error: File '${filePath}' not found`;
        }

        const stat = fs.statSync(resolvedPath);
        if (!stat.isFile()) {
          return `Error: '${filePath}' is not a file`;
        }

        let content: string;
        try {
          content = fs.readFileSync(resolvedPath, "utf-8");
        } catch {
          return `Error: File contains non-UTF-8 content`;
        }

        if (!content.includes(old_str)) {
          return `Error: String not found in file`;
        }

        const occurrences = (
          content.match(
            new RegExp(old_str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g"),
          ) || []
        ).length;
        if (occurrences > 1) {
          return `Error: String appears ${occurrences} times. Please provide more specific context.`;
        }

        const newContent = content.replace(old_str, new_str);
        fs.writeFileSync(resolvedPath, newContent, "utf-8");

        return `Successfully replaced text in '${filePath}'`;
      } else if (command === "create") {
        if (file_text === undefined) {
          return "Error: create command requires file_text parameter";
        }

        if (fs.existsSync(resolvedPath)) {
          return `Error: File '${filePath}' already exists`;
        }

        const dir = path.dirname(resolvedPath);
        if (!fs.existsSync(dir)) {
          fs.mkdirSync(dir, { recursive: true });
        }

        fs.writeFileSync(resolvedPath, file_text, "utf-8");
        return `Successfully created file '${filePath}'`;
      } else if (command === "insert") {
        if (new_str === undefined || insert_line === undefined) {
          return "Error: insert command requires both new_str and insert_line parameters";
        }

        if (!fs.existsSync(resolvedPath)) {
          return `Error: File '${filePath}' not found`;
        }

        const stat = fs.statSync(resolvedPath);
        if (!stat.isFile()) {
          return `Error: '${filePath}' is not a file`;
        }

        let content: string;
        try {
          content = fs.readFileSync(resolvedPath, "utf-8");
        } catch {
          return `Error: File contains non-UTF-8 content`;
        }

        const lines = content.split("\n");

        if (insert_line < 0 || insert_line > lines.length) {
          return `Error: insert_line ${insert_line} out of range (0-${lines.length})`;
        }

        let insertText = new_str;
        if (insertText && !insertText.endsWith("\n")) {
          insertText += "\n";
        }

        lines.splice(insert_line, 0, insertText.slice(0, -1)); // Remove the added newline since split/join handles it

        fs.writeFileSync(resolvedPath, lines.join("\n"), "utf-8");
        return `Successfully inserted text at line ${insert_line} in '${filePath}'`;
      } else {
        return `Error: Unknown command '${command}'`;
      }
    } catch (e) {
      return `Error: ${String(e)}`;
    }
  },
  {
    name: "str_replace_based_edit_tool",
    description:
      "Edit files using string replacement, view, create, or insert operations",
    schema: z.object({
      command: z
        .enum(["view", "str_replace", "create", "insert"])
        .describe("Action to perform"),
      path: z
        .string()
        .describe("Path to the file (relative to working directory)"),
      view_range: z
        .tuple([z.number(), z.number()])
        .optional()
        .describe("Line range for view command [start, end]"),
      old_str: z
        .string()
        .optional()
        .describe("String to replace (required for str_replace)"),
      new_str: z
        .string()
        .optional()
        .describe("Replacement string (required for str_replace and insert)"),
      file_text: z
        .string()
        .optional()
        .describe("Content for new file (required for create)"),
      insert_line: z
        .number()
        .optional()
        .describe("Line number to insert at (required for insert)"),
    }),
  },
);

// Custom grep tool that respects working_directory from state
export const grep = tool(
  async (
    {
      pattern,
      files,
      search_path,
      file_pattern = "*",
      max_results = 50,
      case_sensitive = false,
      context_lines = 0,
      regex = false,
    }: {
      pattern: string;
      files?: string | string[];
      search_path?: string;
      file_pattern?: string;
      max_results?: number;
      case_sensitive?: boolean;
      context_lines?: number;
      regex?: boolean;
    },
    config,
  ) => {
    return new Promise<string>((resolve) => {
      try {
        // Get working directory from state
        const currentTaskInput =
          config?.configurable?.__pregel_scratchpad?.currentTaskInput;
        const workingDirectory =
          currentTaskInput?.working_directory || process.cwd();

        if (!files && !search_path) {
          resolve(
            "Error: Must provide either 'files' parameter or 'search_path' parameter",
          );
          return;
        }

        const cmd = ["rg"];

        if (regex) {
          cmd.push("-e", pattern);
        } else {
          cmd.push("-F", pattern);
        }

        if (!case_sensitive) {
          cmd.push("-i");
        }

        if (context_lines > 0) {
          cmd.push("-C", context_lines.toString());
        }

        if (max_results > 0) {
          cmd.push("-m", max_results.toString());
        }

        if (file_pattern !== "*") {
          cmd.push("-g", file_pattern);
        }

        // Resolve file paths relative to working directory
        if (files) {
          if (typeof files === "string") {
            const resolvedFile = path.resolve(workingDirectory, files);
            cmd.push(resolvedFile);
          } else {
            const resolvedFiles = files.map((f) =>
              path.resolve(workingDirectory, f),
            );
            cmd.push(...resolvedFiles);
          }
        } else if (search_path) {
          const resolvedSearchPath = path.resolve(
            workingDirectory,
            search_path,
          );
          cmd.push(resolvedSearchPath);
        }

        const child = spawn(cmd[0], cmd.slice(1), {
          cwd: workingDirectory,
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

        const timeout = setTimeout(() => {
          child.kill();
          resolve("Error: ripgrep search timed out");
        }, 30000);

        child.on("close", (code) => {
          clearTimeout(timeout);

          if (code === 0) {
            resolve(stdout);
          } else if (code === 1) {
            const patternDesc = regex
              ? `regex pattern '${pattern}'`
              : `text '${pattern}'`;
            const caseDesc = case_sensitive
              ? " (case-sensitive)"
              : " (case-insensitive)";
            resolve(`No matches found for ${patternDesc}${caseDesc}`);
          } else {
            resolve(`Error running ripgrep: ${stderr}`);
          }
        });

        child.on("error", (err) => {
          clearTimeout(timeout);
          if (err.message.includes("ENOENT")) {
            resolve(
              "Error: ripgrep (rg) not found. Please install ripgrep to use this tool.",
            );
          } else {
            resolve(`Error running ripgrep: ${String(err)}`);
          }
        });
      } catch (e) {
        resolve(`Error in grep search: ${String(e)}`);
      }
    });
  },
  {
    name: "grep",
    description: "Search for text patterns in files using ripgrep",
    schema: z.object({
      pattern: z.string().describe("Text pattern to search for"),
      files: z
        .union([z.string(), z.array(z.string())])
        .optional()
        .describe(
          "Specific files to search in (relative to working directory)",
        ),
      search_path: z
        .string()
        .optional()
        .describe("Directory to search in (relative to working directory)"),
      file_pattern: z
        .string()
        .optional()
        .default("*")
        .describe("File pattern to filter by"),
      max_results: z
        .number()
        .optional()
        .default(50)
        .describe("Maximum number of results"),
      case_sensitive: z
        .boolean()
        .optional()
        .default(false)
        .describe("Case sensitive search"),
      context_lines: z
        .number()
        .optional()
        .default(0)
        .describe("Number of context lines around matches"),
      regex: z
        .boolean()
        .optional()
        .default(false)
        .describe("Use regex pattern matching"),
    }),
  },
);

// Custom glob tool that respects working_directory from state
export const glob = tool(
  (
    {
      pattern,
      path: searchPath = ".",
      absolute = false,
      ignore_case = false,
    }: {
      pattern: string;
      path?: string;
      absolute?: boolean;
      ignore_case?: boolean;
    },
    config,
  ) => {
    try {
      const currentTaskInput =
        config?.configurable?.__pregel_scratchpad?.currentTaskInput;
      const workingDirectory =
        currentTaskInput?.working_directory || process.cwd();

      const resolvedSearchPath = path.resolve(workingDirectory, searchPath);

      const results: string[] = [];

      function matchesPattern(
        filePath: string,
        pattern: string,
        ignoreCase: boolean,
      ): boolean {
        const regexPattern = pattern
          .replace(/\\/g, "\\\\")
          .replace(/\*\*/g, ".*")
          .replace(/\*/g, "[^/]*")
          .replace(/\?/g, "[^/]")
          .replace(/\./g, "\\.");

        if (ignoreCase) {
          return new RegExp(`^${regexPattern}$`, "i").test(filePath);
        }
        return new RegExp(`^${regexPattern}$`).test(filePath);
      }

      function walkDirectory(dir: string, currentPath = ""): void {
        try {
          const items = fs.readdirSync(dir);

          for (const item of items) {
            const fullPath = path.join(dir, item);
            const relativePath = currentPath
              ? path.join(currentPath, item)
              : item;

            const stat = fs.statSync(fullPath);

            if (stat.isDirectory()) {
              if (matchesPattern(relativePath, pattern, ignore_case)) {
                results.push(absolute ? fullPath : relativePath);
              }
              walkDirectory(fullPath, relativePath);
            } else if (stat.isFile()) {
              if (matchesPattern(relativePath, pattern, ignore_case)) {
                results.push(absolute ? fullPath : relativePath);
              }
            }
          }
        } catch {
          // Error occurred while reading directory, continue with other items
        }
      }

      // Start walking from resolved search path
      walkDirectory(resolvedSearchPath);

      return results.sort();
    } catch (e) {
      return [`Error in glob search: ${String(e)}`];
    }
  },
  {
    name: "glob",
    description: "Find files and directories matching a glob pattern",
    schema: z.object({
      pattern: z
        .string()
        .describe("Glob pattern to match files (supports *, **, ?)"),
      path: z
        .string()
        .optional()
        .default(".")
        .describe(
          "Directory path to search in (relative to working directory)",
        ),
      absolute: z
        .boolean()
        .optional()
        .default(false)
        .describe("Return absolute paths instead of relative"),
      ignore_case: z
        .boolean()
        .optional()
        .default(false)
        .describe("Case-insensitive pattern matching"),
    }),
  },
);

export const writeTodos = tool(
  (input, config) => {
    return new Command({
      update: {
        todos: input.todos,
        messages: [
          new ToolMessage({
            content: `Updated todo list to ${JSON.stringify(input.todos)}`,
            tool_call_id: config.toolCall?.id as string,
          }),
        ],
      },
    });
  },
  {
    name: "write_todos",
    description: "Update the todo list with new todo items",
    schema: z.object({
      todos: z
        .array(
          z.object({
            content: z.string().describe("Content of the todo item"),
            status: z
              .enum(["pending", "in_progress", "completed"])
              .describe("Status of the todo"),
          }),
        )
        .describe("List of todo items to update"),
    }),
  },
);
