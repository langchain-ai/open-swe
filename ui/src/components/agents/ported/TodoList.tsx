import { useRef, useEffect, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  ListChecks,
  Loader2,
} from "lucide-react";
import type { TodoItem } from "@/lib/agents/types";

interface TodoListProps {
  todos: TodoItem[];
  className?: string;
  runActive?: boolean;
}

function StatusIcon({ status }: { status: TodoItem["status"] }) {
  switch (status) {
    case "pending":
      return <Circle className="h-3.5 w-3.5 text-[color:var(--ui-text-dim)] shrink-0" />;
    case "in_progress":
      return <Loader2 className="h-3.5 w-3.5 text-[color:var(--ui-accent)] animate-spin shrink-0" />;
    case "completed":
      return <CheckCircle2 className="h-3.5 w-3.5 text-green-400 shrink-0" />;
  }
}

const MAX_HEIGHT = 160;

export function TodoList({ todos, className = "", runActive = false }: TodoListProps) {
  const [collapsed, setCollapsed] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const prevRunActiveRef = useRef(runActive);

  useEffect(() => {
    if (prevRunActiveRef.current && !runActive) {
      setCollapsed(true);
    }
    prevRunActiveRef.current = runActive;
  }, [runActive]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const firstActive = todos.findIndex((t) => t.status === "in_progress");
    const targetIndex = firstActive !== -1 ? firstActive : todos.length - 1;
    const targetEl = el.children[targetIndex] as HTMLElement | undefined;
    targetEl?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [todos]);

  if (todos.length === 0) return null;

  const completed = todos.filter((t) => t.status === "completed").length;

  return (
    <div className={`font-sans text-xs rounded-xl border border-[var(--ui-border)] bg-[var(--ui-code-bubble)] overflow-hidden ${className}`}>
      <button
        type="button"
        className="w-full px-3 py-2 flex items-center gap-2 text-left hover:bg-white/5 transition-colors"
        onClick={() => setCollapsed((c) => !c)}
      >
        <ListChecks className="h-3.5 w-3.5 text-[color:var(--ui-text-muted)] shrink-0" />
        <span className="text-[color:var(--ui-text-muted)] truncate">
          {completed} out of {todos.length} task{todos.length === 1 ? "" : "s"} completed
        </span>
        {collapsed ? (
          <ChevronRight className="h-3.5 w-3.5 text-[color:var(--ui-text-dim)] ml-auto shrink-0" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5 text-[color:var(--ui-text-dim)] ml-auto shrink-0" />
        )}
      </button>

      {!collapsed && (
        <div
          ref={scrollRef}
          className="border-t border-[var(--ui-border)] px-3 py-2 space-y-1.5 overflow-y-auto"
          style={{ maxHeight: MAX_HEIGHT }}
        >
          {todos.map((todo, index) => (
            <div
              key={index}
              className={`flex items-start gap-2 ${todo.status === "completed" ? "opacity-70" : ""}`}
            >
              <StatusIcon status={todo.status} />
              <span
                className={
                  todo.status === "completed"
                    ? "text-[color:var(--ui-text-dim)] line-through"
                    : todo.status === "in_progress"
                      ? "text-[color:var(--ui-text)]"
                      : "text-[color:var(--ui-text-muted)]"
                }
              >
                <span className="mr-1 text-[color:var(--ui-text-dim)]">{index + 1}.</span>
                {todo.content}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
