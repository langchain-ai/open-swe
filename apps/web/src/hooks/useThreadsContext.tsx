import { useContext } from "react";
import { ThreadContext } from "@/providers/thread-context";

export function useThreadsContext() {
  const context = useContext(ThreadContext);
  if (context === undefined) {
    throw new Error("useThreadsContext must be used within a ThreadProvider");
  }
  return context;
}
