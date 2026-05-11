import { Box, Text } from "ink";
import { MessageResponse } from "@tui/components/MessageResponse.js";
import { themeColor } from "@tui/theme.js";
import type { ToolUI } from "./types.js";

type Todo = {
  content?: string;
  task?: string;
  text?: string;
  status?: string;
  state?: string;
};

const todoLabel = (todo: Todo): string =>
  todo.content ?? todo.task ?? todo.text ?? "";

const todoStatus = (todo: Todo): string =>
  (todo.status ?? todo.state ?? "pending").toLowerCase();

const STATUS_GLYPH: Record<string, string> = {
  completed: "☒",
  done: "☒",
  in_progress: "☐",
  pending: "☐",
  cancelled: "☒",
};

export const TodoWriteTool: ToolUI = {
  names: ["write_todos", "todo_write", "todowrite"],
  userFacingName: () => "Update Todos",
  renderToolUseMessage: () => "",
  renderToolResultMessage: (_output, { args }) => {
    if (!args) return null;
    const todos = Array.isArray(args.todos) ? (args.todos as Todo[]) : [];
    if (todos.length === 0) return null;
    const subtle = themeColor("subtle");
    const success = themeColor("success");
    const inactive = themeColor("inactive");
    return (
      <MessageResponse>
        <Box flexDirection="column">
          {todos.map((todo, idx) => {
            const status = todoStatus(todo);
            const glyph = STATUS_GLYPH[status] ?? "☐";
            const isDone = status === "completed" || status === "done";
            const isActive = status === "in_progress";
            const color = isDone ? success : isActive ? undefined : subtle;
            return (
              <Box key={idx}>
                <Text color={isDone ? success : inactive}>{glyph} </Text>
                <Text color={color} strikethrough={isDone} bold={isActive}>
                  {todoLabel(todo)}
                </Text>
              </Box>
            );
          })}
        </Box>
      </MessageResponse>
    );
  },
};
