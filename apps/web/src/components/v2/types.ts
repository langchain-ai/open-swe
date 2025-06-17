// Base types from the API
export type Metadata = Record<string, any>

export type ThreadStatus = "idle" | "busy" | "interrupted" | "error"

export type Interrupt = {
  value: any
  when: "during" | "after"
}

// Message types
export interface BaseMessage {
  content: string
  type: "ai" | "human" | "tool"
  timestamp?: string
}

// Plan and Task types
export type PlanItem = {
  /**
   * The index of the plan item. This is the order in which
   * it should be executed.
   */
  index: number
  /**
   * The actual task to perform.
   */
  plan: string
  /**
   * Whether or not the plan item has been completed.
   */
  completed: boolean
  /**
   * A summary of the completed task.
   */
  summary?: string
}

export type PlanRevision = {
  /**
   * The revision index of the plan.
   * This is used to track edits made to the plan by the agent or user
   */
  revisionIndex: number
  /**
   * The plans for this task & revision.
   */
  plans: PlanItem[]
  /**
   * Timestamp when this revision was created
   */
  createdAt: number
  /**
   * Who created this revision (agent or user)
   */
  createdBy: "agent" | "user"
}

export type Task = {
  /**
   * Unique identifier for the task
   */
  id: string
  /**
   * The index of the user's task in chronological order
   */
  taskIndex: number
  /**
   * The original user request that created this task
   */
  request: string
  /**
   * When the task was created
   */
  createdAt: number
  /**
   * Whether the task is completed
   */
  completed: boolean
  /**
   * When the task was completed (if applicable)
   */
  completedAt?: number
  /**
   * Overall summary of the completed task
   */
  summary?: string
  /**
   * The plans generated for this task.
   * Ordered by revisionIndex, with the latest revision being the active one
   */
  planRevisions: PlanRevision[]
  /**
   * Index of the currently active plan revision
   */
  activeRevisionIndex: number
  /**
   * Optional parent task id if this task was derived from another task
   */
  parentTaskId?: string
}

export type TaskPlan = {
  /**
   * All tasks in the system
   */
  tasks: Task[]
  /**
   * Index of the currently active task
   */
  activeTaskIndex: number
}

export type TargetRepository = {
  owner: string
  repo: string
  branch?: string
  baseCommit?: string
}

export interface GraphAnnotation {
  messages: BaseMessage[]
  internalMessages: BaseMessage[]
  taskPlan: TaskPlan
  planContextSummary: string
  sandboxSessionId: string
  branchName: string
  targetRepository: TargetRepository
  codebaseTree: string
  githubIssueId: number
}

// Main Thread interface
export interface Thread<ValuesType = GraphAnnotation> {
  /** The ID of the thread. */
  thread_id: string
  /** The time the thread was created. */
  created_at: string
  /** The last time the thread was updated. */
  updated_at: string
  /** The thread metadata. */
  metadata: Metadata
  /** The status of the thread */
  status: ThreadStatus
  /** The current state of the thread. */
  values: ValuesType
  /** Interrupts which were thrown in this thread */
  interrupts: Record<string, Array<Interrupt>>
}

// Legacy types for backward compatibility and UI helpers
export interface ActionStep {
  id: string
  title: string
  status: "preparing" | "executing" | "applying" | "completed" | "failed"
  type: "preparation" | "command" | "file_edit"
  command?: string
  workingDirectory?: string
  output?: string
  filePath?: string
  diff?: string
}

// Helper types for UI display
export interface ThreadDisplayInfo {
  id: string
  title: string
  status: "running" | "completed" | "failed" | "pending"
  lastActivity: string
  taskCount: number
  repository: string
  branch: string
  githubIssue?: {
    number: number
    url: string
  }
  pullRequest?: {
    number: number
    url: string
    status: "draft" | "open" | "merged" | "closed"
  }
}

// Utility functions to convert between Thread and ThreadDisplayInfo
export function threadToDisplayInfo(thread: Thread): ThreadDisplayInfo {
  const values = thread.values
  const currentTask = values.taskPlan.tasks[values.taskPlan.activeTaskIndex]
  const completedTasks = values.taskPlan.tasks.filter((t) => t.completed).length

  // Determine UI status from thread status and task completion
  let uiStatus: ThreadDisplayInfo["status"]
  switch (thread.status) {
    case "busy":
      uiStatus = "running"
      break
    case "idle":
      uiStatus = completedTasks === values.taskPlan.tasks.length ? "completed" : "pending"
      break
    case "error":
      uiStatus = "failed"
      break
    case "interrupted":
      uiStatus = "pending"
      break
    default:
      uiStatus = "pending"
  }

  // Calculate time since last update
  const lastUpdate = new Date(thread.updated_at)
  const now = new Date()
  const diffMs = now.getTime() - lastUpdate.getTime()
  const diffMins = Math.floor(diffMs / (1000 * 60))
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60))
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))

  let lastActivity: string
  if (diffMins < 1) {
    lastActivity = "just now"
  } else if (diffMins < 60) {
    lastActivity = `${diffMins} min ago`
  } else if (diffHours < 24) {
    lastActivity = `${diffHours} hour${diffHours > 1 ? "s" : ""} ago`
  } else {
    lastActivity = `${diffDays} day${diffDays > 1 ? "s" : ""} ago`
  }

  return {
    id: thread.thread_id,
    title: currentTask?.request || "Untitled Task",
    status: uiStatus,
    lastActivity,
    taskCount: values.taskPlan.tasks.length,
    repository: `${values.targetRepository.owner}/${values.targetRepository.repo}`,
    branch: values.targetRepository.branch || "main",
    githubIssue: values.githubIssueId
      ? {
          number: values.githubIssueId,
          url: `https://github.com/${values.targetRepository.owner}/${values.targetRepository.repo}/issues/${values.githubIssueId}`,
        }
      : undefined,
    // Note: PR info would need to be added to GraphAnnotation or derived from metadata
  }
}
