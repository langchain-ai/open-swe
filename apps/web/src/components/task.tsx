"use client";
import {
  CheckCircle2,
  XCircle,
  Pause,
  Loader2,
  Github,
  Calendar,
} from "lucide-react";
import { motion } from "framer-motion";
import { TaskWithStatus, TaskStatus } from "@/types/index";

// TODO: Fix Const export to fix flash reload issue
// TODO: create a new state for pending tasks that arent running yet?
export const formatTaskTitle = (
  plan: string,
  maxLength: number = 60,
): string => {
  if (!plan) return "Untitled Task";
  let title = plan.trim();
  title = title.replace(/(^\w|\.\s+\w)/g, (match) => match.toUpperCase());
  if (title.length > maxLength) {
    const truncated = title.substring(0, maxLength);
    const lastSpace = truncated.lastIndexOf(" ");
    if (lastSpace > maxLength * 0.7) {
      title = truncated.substring(0, lastSpace);
    } else {
      title = truncated + "...";
    }
  }

  return title;
};

export const getCurrentTask = (
  plan: TaskWithStatus[],
): TaskWithStatus | null => {
  return (
    plan.filter((p) => !p.completed).sort((a, b) => a.index - b.index)[0] ||
    null
  );
};

export const computeTaskStatus = (
  task: TaskWithStatus,
  currentTask: TaskWithStatus | null,
  isLoading: boolean,
  hasError: boolean,
  hasInterrupt: boolean,
): TaskStatus => {
  if (task.completed) {
    return "done";
  }

  const isCurrentTask = currentTask?.index === task.index;

  if (isCurrentTask) {
    if (hasError) {
      return "error";
    }

    if (hasInterrupt) {
      return "interrupted";
    }

    if (isLoading) {
      return "running";
    }
  }

  // Future tasks that aren't running are in a pending state
  // We'll show them as "interrupted" for now (could be a different status)
  return "interrupted";
};

const StatusIndicator = ({ status }: { status: TaskStatus }) => {
  switch (status) {
    case "running":
      return <Loader2 className="h-4 w-4 animate-spin text-blue-500" />;
    case "interrupted":
      return <Pause className="h-4 w-4 text-yellow-500" />;
    case "done":
      return <CheckCircle2 className="h-4 w-4 text-green-500" />;
    case "error":
      return <XCircle className="h-4 w-4 text-red-500" />;
    default:
      return null;
  }
};

export const Task = ({ task }: { task: TaskWithStatus }) => {
  const formattedTitle = formatTaskTitle(task.plan);

  return (
    <motion.div
      className="group flex cursor-pointer items-start gap-3 border-b border-gray-100 py-3 transition-colors duration-200 last:border-b-0 hover:bg-gray-50"
      whileTap={{ opacity: 0.8 }}
      transition={{ duration: 0.1 }}
    >
      <div className="mt-1 flex-shrink-0">
        <StatusIndicator status={task.status} />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <h3 className="mb-1 truncate text-sm font-medium text-gray-900 group-hover:text-gray-700">
              {formattedTitle}
            </h3>
            <div className="flex items-center gap-2 text-xs text-gray-500">
              {task.date && (
                <span className="flex items-center gap-1">
                  <Calendar className="h-3 w-3 text-gray-400" />
                  <span>{task.date}</span>
                </span>
              )}
              {task.date && task.repository && <span>·</span>}
              {task.repository && (
                <span className="flex items-center gap-1">
                  <Github className="h-3 w-3 text-gray-400" />
                  <span>{task.repository}</span>
                </span>
              )}
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
};
