"use client";

import {
  createContext,
  useContext,
  useState,
  ReactNode,
  useCallback,
} from "react";
import { TaskPlan } from "@open-swe/shared/open-swe/types";

interface TaskPlanContextValue {
  taskPlans: Record<string, TaskPlan>;
  setTaskPlan: (threadId: string, taskPlan: TaskPlan | undefined) => void;
  getTaskPlan: (threadId: string) => TaskPlan | undefined;
}

const TaskPlanContext = createContext<TaskPlanContextValue | undefined>(
  undefined,
);

interface TaskPlanProviderProps {
  children: ReactNode;
}

export function TaskPlanProvider({ children }: TaskPlanProviderProps) {
  const [taskPlans, setTaskPlans] = useState<Record<string, TaskPlan>>({});

  const setTaskPlan = useCallback(
    (threadId: string, taskPlan: TaskPlan | undefined) => {
      setTaskPlans((prev) => {
        if (taskPlan === undefined) {
          const { [threadId]: removed, ...rest } = prev;
          return rest;
        }
        return {
          ...prev,
          [threadId]: taskPlan,
        };
      });
    },
    [],
  );

  const getTaskPlan = useCallback(
    (threadId: string): TaskPlan | undefined => {
      return taskPlans[threadId];
    },
    [taskPlans],
  );

  return (
    <TaskPlanContext.Provider value={{ taskPlans, setTaskPlan, getTaskPlan }}>
      {children}
    </TaskPlanContext.Provider>
  );
}

export function useTaskPlanContext() {
  const context = useContext(TaskPlanContext);
  if (context === undefined) {
    throw new Error(
      "useTaskPlanContext must be used within a TaskPlanProvider",
    );
  }
  return context;
}
