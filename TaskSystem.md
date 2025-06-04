# Task System Documentation

## Overview

The task system manages the display and interaction with AI agent tasks organized by threads. Tasks represent individual steps in an AI workflow, while threads group related tasks together based on conversation sessions.

## Architecture Overview

### Core Data Flow
```
LangGraph API → TaskProvider → Components (TaskList, TaskListSidebar) → UI
Thread API → ThreadProvider → Stream Context → Task Resolution
```

### Key Providers
1. **TaskProvider** (`/providers/Task.tsx`) - Primary task data management
2. **ThreadProvider** (`/providers/Thread.tsx`) - Thread metadata management  
3. **StreamProvider** (`/providers/Stream.tsx`) - Real-time communication & state

## Core Files & Components

### Data Layer

#### TaskProvider (`/providers/Task.tsx`)
**Purpose**: Manages all task-related data and API calls
**Key Functions**:
- `getAllTasks()` - Fetches tasks from all threads with enhanced metadata
- `getTasks(threadId)` - Fetches tasks for a specific thread
- `createTaskId()` - Creates deterministic task IDs from thread+content

**State Management**:
- `tasks: PlanItem[]` - Raw tasks for current thread
- `allTasks: TaskWithContext[]` - Enhanced tasks with thread context
- `tasksLoading: boolean` - Loading state

**Data Transformation**: Converts `PlanItem[]` → `TaskWithContext[]` by:
- Adding generated `taskId`, `threadId`, `repository`, `branch`
- Extracting thread titles from message content
- Determining task status from completion state

#### ThreadProvider (`/providers/Thread.tsx`)  
**Purpose**: Manages thread metadata and navigation
**Key Functions**:
- `getThreads()` - Fetches thread summaries
**State**: `threads: Thread[]` - Basic thread metadata

#### StreamProvider (`/providers/Stream.tsx`)
**Purpose**: Real-time communication with LangGraph API
**Key Features**:
- WebSocket-based streaming
- Thread creation and management
- Message handling
- Authentication state

### UI Components

#### TaskList (`/components/task-list.tsx`)
**Purpose**: Main dashboard view showing threads and tasks
**Key Features**:
- Groups tasks by thread (`ThreadSummary[]`)
- Pagination (5 threads per page)
- Collapsible thread view with task details
- Thread status computation based on task states

#### TaskListSidebar (`/components/task-list-sidebar.tsx`)
**Purpose**: Compact sidebar view for task navigation
**Key Features**:
- Similar grouping logic to TaskList
- Higher density (10 threads per page)
- Selected task highlighting
- Quick navigation between tasks

#### Task Component (`/components/task.tsx`)
**Purpose**: Individual task display component
**Key Features**:
- Status indicators (running, done, interrupted, error)
- Task title formatting
- Repository and date metadata
- **⚠️ UNUSED**: Not currently used in TaskList/TaskListSidebar

## Data Types & Interfaces

### Core Types
```typescript
// Base task from API
interface PlanItem {
  index: number;
  plan: string;        // Task description
  completed: boolean;
  summary?: string;
}

// Enhanced task with thread context
interface TaskWithContext extends PlanItem {
  taskId: string;      // Generated UUID
  threadId: string;    // Reference to thread
  threadTitle?: string;
  repository?: string;
  branch?: string;
  date: string;
  createdAt: string;
  status: "running" | "interrupted" | "done" | "error";
}

// Thread summary for grouping
interface ThreadSummary {
  threadId: string;
  threadTitle: string;
  repository: string;
  branch: string;
  date: string;
  createdAt: string;
  tasks: TaskWithContext[];
  completedTasksCount: number;
  totalTasksCount: number;
  status: "running" | "interrupted" | "done" | "error";
}
```

## Task-Thread Relationship

### Data Persistence
- **Threads**: Persisted in LangGraph API via thread management
- **Tasks**: Stored as `plan` or `proposedPlan` arrays in thread values
- **Navigation**: Uses deterministic task IDs for URL routing

### Thread Discovery
1. `TaskProvider.getAllTasks()` searches for threads by `assistant_id`/`graph_id`
2. Fetches individual thread details for task extraction
3. Checks both `plan` and `proposedPlan` fields
4. Falls back to `proposedPlan` if `plan` is empty

### Status Resolution
- **Task Status**: Based on `completed` field + current execution state
- **Thread Status**: Computed from constituent task states
  - `error`: Any task has error
  - `running`: Any task is running  
  - `done`: All tasks completed
  - `interrupted`: Default fallback

## Current Issues & Problems

### ✅ Recently Fixed

#### 1. GitHub Modal Persistence ✅ 
**Fixed**: Added localStorage caching for GitHub app installation status
**Solution**: Installation status is now cached and only checked once per session
**Impact**: Modal no longer appears on every page refresh

#### 2. Sidebar Collapse Button ✅
**Fixed**: Added collapse button in sidebar header with persistent state
**Solution**: Uses existing `chatHistoryOpen` querystate for persistence  
**Impact**: Users can now collapse the sidebar and state persists across reloads

#### 3. Thread Navigation ✅
**Fixed**: Clicking thread header now navigates to chat mode with thread loaded
**Solution**: Separate click handlers for thread navigation vs expand/collapse
**Impact**: Users can now directly access threads from the task list/sidebar

#### 4. Data Type Consistency ✅
**Fixed**: Proper handling of both string tasks and PlanItem objects
**Solution**: Robust type checking and data transformation in TaskProvider
**Impact**: Task descriptions now display correctly instead of "No task description"

### 🔴 Critical Issues

#### 1. Data Persistence Loss
**Problem**: Tasks/threads disappearing on server restart
**Root Cause**: May be related to LangGraph API data retention or client-side caching
**Status**: Needs investigation - TaskProvider does persist via API

#### 2. Unused Task Component
**Problem**: `Task` component exists but isn't used in TaskList/TaskListSidebar
**Issue**: Direct `task.plan` rendering instead of using proper component
**Impact**: Inconsistent UI, lost formatting, missing functionality
**Status**: Pending implementation

#### 3. Complex ID Generation
**Problem**: Overcomplicated `createTaskId()` function with hash-based generation
**Issue**: Hard to debug, potential collisions, not human-readable
**Impact**: Navigation issues, debugging complexity
**Status**: Pending simplification

### 🔶 Architectural Issues

#### 1. Provider Confusion
**Problem**: Multiple providers (Task, Thread, Stream) with overlapping concerns
**Issue**: Unclear data ownership, potential state conflicts
**Impact**: Complexity, potential race conditions

#### 2. Redundant Data Fetching
**Problem**: Both TaskList and TaskListSidebar call `getAllTasks()`
**Issue**: Duplicate API calls, inconsistent state
**Impact**: Performance, potential data inconsistencies

#### 3. Inconsistent State Management
**Problem**: Mix of local state, URL params, and provider state
**Issue**: State synchronization problems
**Impact**: UI inconsistencies, navigation bugs

#### 4. Tight Coupling
**Problem**: Components tightly coupled to specific data structures
**Issue**: Hard to modify, test, or extend
**Impact**: Maintenance difficulties

## Recommended Improvements

### 🎯 Immediate Fixes

#### 1. Fix Data Type Issues
```typescript
// Ensure consistent task data structure
const taskDescription = typeof task === 'string' ? task : task.plan;
```

#### 2. Use Task Component
```typescript
// Replace direct rendering with proper component
{thread.tasks.map((task) => (
  <Task key={task.taskId} task={task} />
))}
```

#### 3. Simplify Task ID Generation
```typescript
// Use simple, predictable IDs
const createTaskId = (threadId: string, index: number) => 
  `${threadId}-task-${index}`;
```

### 🏗️ Architectural Improvements

#### 1. Consolidate Providers
**Merge TaskProvider and ThreadProvider** into unified `DataProvider`:
```typescript
interface DataProvider {
  threads: Thread[];
  tasks: TaskWithContext[];
  selectedThread: string | null;
  selectedTask: string | null;
  // Unified data fetching
  fetchData: () => Promise<void>;
}
```

#### 2. Implement Proper Caching
```typescript
// Add caching layer to prevent unnecessary API calls
const useTaskCache = () => {
  const [cache, setCache] = useState<Map<string, TaskWithContext[]>>();
  // Cache implementation
};
```

#### 3. Separate Data & UI Concerns
```typescript
// Custom hooks for data logic
const useTaskData = () => { /* data logic */ };
const useTaskNavigation = () => { /* navigation logic */ };

// Keep components purely presentational
const TaskList = ({ tasks, onTaskClick }) => { /* UI only */ };
```

#### 4. Standardize Error Handling
```typescript
// Consistent error handling pattern
const useAsyncOperation = (operation) => {
  const [data, setData] = useState();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // Standard async handling
};
```

### 🎨 Code Quality Improvements

#### 1. Extract Business Logic
- Move thread grouping logic to utilities
- Create reusable status computation functions
- Standardize data transformation pipelines

#### 2. Improve Type Safety
- Use discriminated unions for task states
- Add proper error types
- Implement runtime type validation

#### 3. Add Testing
- Unit tests for data transformations
- Integration tests for provider interactions
- Component testing for UI behavior

#### 4. Performance Optimization
- Implement virtual scrolling for large task lists
- Add memo optimization for expensive computations
- Use React Query for data fetching/caching

## Migration Strategy

### Phase 1: Fix Critical Issues (Week 1)
1. Fix data type inconsistencies
2. Implement proper task component usage
3. Identify and fix persistence issues

### Phase 2: Simplify Architecture (Week 2)
1. Consolidate providers
2. Implement caching layer
3. Standardize error handling

### Phase 3: Code Quality (Week 3)
1. Extract business logic
2. Add comprehensive testing
3. Performance optimization

### Phase 4: Polish & Documentation (Week 4)
1. Final bug fixes
2. Complete documentation
3. User experience improvements

## Testing Strategy

### Unit Tests
- Data transformation functions
- Status computation logic
- ID generation utilities

### Integration Tests  
- Provider interactions
- API data flow
- State synchronization

### E2E Tests
- Task navigation workflows
- Thread switching scenarios
- Data persistence verification

## Provider Overlap Analysis & Solution

### Current State ✅ 
**Persistence**: Tasks DO persist via LangGraph API - this is working correctly.

**The Real Problem**: Provider redundancy and unclear responsibilities:

### Provider Responsibilities (Current)
1. **TaskProvider** ✅ Core - Fetches threads + extracts/transforms tasks
2. **ThreadProvider** ❓ Redundant - Also fetches threads but only metadata  
3. **StreamProvider** ✅ Core - Real-time communication, thread creation

### 🎯 Consolidation Plan

#### Option 1: Keep TaskProvider, Remove ThreadProvider (Recommended)
```typescript
// TaskProvider already does everything ThreadProvider does + more
// Just extend TaskProvider to include thread metadata

interface TaskContextType {
  // Existing task functionality
  getAllTasks: () => Promise<TaskWithContext[]>;
  getTasks: (threadId: string) => Promise<PlanItem[]>;
  
  // Add thread metadata (merge ThreadProvider functionality)
  threads: Thread[];
  getThreads: () => Promise<Thread[]>;
  
  // Unified state
  allTasks: TaskWithContext[];
  tasksLoading: boolean;
  threadsLoading: boolean;
}
```

#### Option 2: Separate Concerns Clearly
```typescript
// ThreadProvider: Pure thread management
// TaskProvider: Pure task management  
// Keep them separate but avoid duplication

// ThreadProvider fetches threads once
// TaskProvider gets tasks from specific threads
// No overlap in API calls
```

### 🚀 Implementation Steps

#### Step 1: Extend TaskProvider (Immediate)
```typescript
// In TaskProvider, add thread management
const [threads, setThreads] = useState<Thread[]>([]);
const [threadsLoading, setThreadsLoading] = useState(false);

const getThreads = useCallback(async (): Promise<Thread[]> => {
  // Move logic from ThreadProvider here
}, [apiUrl, assistantId]);
```

#### Step 2: Update Components  
```typescript
// Components can now use single provider
const { allTasks, threads, tasksLoading } = useTasks();
// Instead of mixing useTasks() and useThreads()
```

#### Step 3: Remove ThreadProvider
```typescript
// Remove ThreadProvider entirely
// Update app.tsx provider nesting
<TaskProvider>
  <StreamProvider>
    {/* No ThreadProvider needed */}
  </StreamProvider>
</TaskProvider>
```

## Conclusion

The task system has a solid foundation but suffers from architectural complexity and data inconsistencies. The primary focus should be on simplifying the data flow, fixing persistence issues, and creating a more maintainable architecture. The recommended improvements will create a more robust, performant, and maintainable system while preserving existing functionality. 