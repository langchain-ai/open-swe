# Task System Documentation

## Overview

The task system manages the display and interaction with AI agent tasks organized by threads. Tasks represent individual steps in an AI workflow (`PlanItem`), while threads group related tasks together based on conversation sessions. The system provides both dashboard views for overview and real-time execution tracking with status inference.

## Architecture Overview

### Core Data Flow
```
LangGraph API → TaskProvider → Components (TaskList, TaskListSidebar, PlanViewer) → UI
Thread API → ThreadProvider → Thread Metadata Management
Stream Context → Real-time Status Updates → Task Status Inference
```

### Key Providers
1. **TaskProvider** (`/providers/Task.tsx`) - Primary task data management with real-time updates
2. **ThreadProvider** (`/providers/Thread.tsx`) - Thread metadata management (still active)
3. **StreamProvider** (`/providers/Stream.tsx`) - Real-time communication & state management

## Core Files & Components

### Data Layer

#### TaskProvider (`/providers/Task.tsx`)
**Purpose**: Manages all task-related data, API calls, and real-time status tracking
**Key Functions**:
- `getAllTasks()` - Fetches tasks from all threads with enhanced metadata and status inference
- `getTasks(threadId)` - Fetches tasks for a specific thread
- `createTaskId()` - Creates simple, predictable task IDs: `${threadId}-${taskIndex}`
- `addActiveThread()` / `removeActiveThread()` - Manages real-time tracking for running threads

**State Management**:
- `tasks: TaskWithStatus[]` - Raw tasks for current thread
- `allTasks: TaskWithContext[]` - Enhanced tasks with thread context
- `tasksLoading: boolean` - Loading state
- `activeThreads: Set<string>` - Tracks threads requiring real-time updates

**Real-time Features**:
- **Polling System**: Automatically polls active threads every 5 seconds for status updates
- **Smart State Updates**: Only re-renders when meaningful changes occur (status, completion, etc.)
- **Auto-detection**: Automatically detects and tracks threads with running/interrupted tasks

**Data Transformation**: Converts `PlanItem[]` → `TaskWithContext[]` by:
- Adding generated `taskId` (simplified format), `threadId`, `repository`, `branch`
- Extracting thread titles from first message content
- **Advanced Status Inference**: Uses `inferTaskStatusWithContext()` for accurate task status
- Handling both string tasks and PlanItem objects from API

#### ThreadProvider (`/providers/Thread.tsx`)  
**Purpose**: Manages basic thread metadata and search functionality
**Key Functions**:
- `getThreads()` - Fetches thread summaries using metadata search
- `getThreadSearchMetadata()` - Handles both UUID and graph_id assistant formats
**State**: `threads: Thread[]` - Basic thread metadata
**Status**: **Active** (contrary to previous documentation recommending removal)

#### StreamProvider (`/providers/Stream.tsx`)
**Purpose**: Real-time communication with LangGraph API
**Key Features**:
- WebSocket-based streaming
- Thread creation and management
- Message handling
- Authentication state
- Integration with TaskProvider for active thread tracking

### UI Components

#### TaskList (`/components/task-list.tsx`)
**Purpose**: Main dashboard view showing threads grouped by tasks
**Key Features**:
- Uses `groupTasksIntoThreads()` utility for thread organization
- Pagination (5 threads per page)
- Tab interface (Threads/Archived)
- Uses `ThreadItem` component for consistent rendering
- Dashboard mode detection (only shows when `!taskId`)

#### TaskListSidebar (`/components/task-list-sidebar.tsx`)
**Purpose**: Compact sidebar view for task navigation
**Key Features**:
- Higher density display (10 threads per page)
- Selected task highlighting with URL state (`useQueryState`)
- Sidebar-specific variant of `ThreadItem`
- Quick navigation between tasks

#### ThreadItem (`/components/thread-item.tsx`)
**Purpose**: Unified component for displaying thread summaries in both dashboard and sidebar
**Key Features**:
- **Dual Variants**: `dashboard` and `sidebar` with different styling/density
- Status indicators with real-time updates from active thread tracking
- Repository/branch display with GitHub icons
- Task completion counters
- Responsive design with proper truncation

#### Task Component (`/components/task.tsx`)
**Purpose**: Individual task display component with formatting and status
**Key Features**:
- Status indicators (running, done, interrupted, error)
- Smart task title formatting with `formatTaskTitle()` utility
- Repository and date metadata display
- **⚠️ STILL UNUSED**: Not currently used in TaskList/TaskListSidebar/ThreadItem
- **Alternative Usage**: Used conceptually in `PlanViewer` component for different rendering

#### PlanViewer (`/components/plan/plan-viewer.tsx`)
**Purpose**: Alternative task rendering for execution plans within chat interface
**Key Features**:
- Linear plan progression view
- Current task highlighting
- Step-by-step execution visualization
- Summary display for completed tasks
- Different UI pattern from dashboard task display

### Utility Layer

#### Thread Utils (`/lib/thread-utils.ts`)
**Purpose**: Business logic for task/thread processing and status inference
**Key Functions**:
- `inferTaskStatusWithContext()` - Advanced status determination using thread state analysis
- `determineThreadStatus()` - Thread-level status based on constituent tasks
- `groupTasksIntoThreads()` - Converts task arrays to thread summaries
- `sortThreadsByDate()` - Chronological sorting of threads
- `hasThreadError()` / `hasThreadInterrupt()` - Error and interrupt detection from messages
- `getCurrentTaskIndex()` - Determines active task in execution sequence

## Data Types & Interfaces

### Core Types
```typescript
// Base task from agent (matches open-swe agent types)
interface PlanItem {
  index: number;
  plan: string;        // Task description
  completed: boolean;
  summary?: string;
}

// Task with computed status
interface TaskWithStatus extends PlanItem {
  status: "running" | "interrupted" | "done" | "error";
  repository?: string;
  date?: string;
}

// Enhanced task with thread context
interface TaskWithContext extends TaskWithStatus {
  taskId: string;      // Simple format: ${threadId}-${taskIndex}
  threadId: string;    // Reference to thread
  threadTitle?: string;
  branch?: string;
  createdAt: string;   // For chronological sorting
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

### Data Persistence ✅
- **Threads**: Persisted in LangGraph API via thread management
- **Tasks**: Stored as `plan` or `proposedPlan` arrays in thread values
- **Navigation**: Uses simplified task IDs for URL routing: `${threadId}-${taskIndex}`
- **Status**: Persistence verified and working correctly

### Thread Discovery & Task Extraction
1. `TaskProvider.getAllTasks()` searches for threads using dual metadata strategy:
   - Primary: `{ graph_id: assistantId }` or `{ assistant_id: assistantId }`
   - Fallback: Alternative search and no-metadata search for debugging
2. Processes up to 200 threads with debouncing (1 second) to prevent API overload
3. Extracts tasks from both `plan` and `proposedPlan` fields
4. Handles mixed data types (string arrays vs PlanItem objects)
5. Enhanced error handling with detailed logging

### Advanced Status Resolution
- **Task Status**: Uses `inferTaskStatusWithContext()` which analyzes:
  - Task completion state
  - Current task index in execution sequence
  - Thread error/interrupt state from message analysis
  - Active thread tracking from real-time system
- **Thread Status**: Computed via `determineThreadStatus()` with priority:
  - `error` > `running` > `done` > `interrupted`
- **Real-time Updates**: Polling system updates status for active threads every 5 seconds

## Current Implementation Status

### ✅ Implemented Features

#### 1. Simplified Task ID Generation
**Status**: ✅ **COMPLETED**
- Simple, predictable format: `${threadId}-${taskIndex}`
- No more hash-based complexity
- Human-readable and debuggable

#### 2. Advanced Status Inference
**Status**: ✅ **COMPLETED**
- Comprehensive status determination logic
- Real-time active thread tracking
- Error and interrupt detection from message analysis
- Current task identification in execution sequence

#### 3. Real-time Updates
**Status**: ✅ **COMPLETED**
- Polling system for active threads
- Smart state updates (only on meaningful changes)
- Auto-detection of potentially active threads
- 5-second polling interval with cleanup

#### 4. Unified Thread Rendering
**Status**: ✅ **COMPLETED**
- `ThreadItem` component with dual variants
- Consistent styling across dashboard and sidebar
- Real-time status indicators

#### 5. Enhanced Data Handling
**Status**: ✅ **COMPLETED**
- Mixed data type support (string/PlanItem)
- Robust error handling and logging
- Debounced API calls
- Comprehensive thread search strategies

### 🔴 Outstanding Issues

#### 1. Unused Task Component
**Problem**: `Task` component exists but isn't used in main UI flows
**Current State**: TaskList/TaskListSidebar use `ThreadItem`, PlanViewer uses custom rendering
**Impact**: Inconsistent task display patterns, potential code duplication
**Recommendation**: Evaluate if `Task` component should be integrated or deprecated

#### 2. Provider Architecture Complexity
**Problem**: Three providers (Task, Thread, Stream) with some overlapping concerns
**Current State**: All three still active, ThreadProvider not removed as previously planned
**Impact**: Complex provider nesting, potential state management issues
**Status**: Requires architectural decision on consolidation vs separation

#### 3. Multiple Task Rendering Patterns
**Problem**: Different components render tasks differently:
- `ThreadItem`: Thread-level aggregation
- `PlanViewer`: Step-by-step execution view
- `Task`: Individual task component (unused)
**Impact**: Potential inconsistency in user experience

## Recommended Next Steps

### 🎯 Immediate Actions

#### 1. Task Component Integration Decision
**Evaluate the role of the Task component**:
```typescript
// Option A: Integrate into ThreadItem for individual task display
{thread.tasks.map((task) => (
  <Task key={task.taskId} task={task} variant="compact" />
))}

// Option B: Deprecate and remove unused component
// Keep current ThreadItem-based approach
```

#### 2. Provider Architecture Review
**Current Setup is Working**: The three-provider system is functional
**Consider**: Whether the complexity is justified by separation of concerns
```typescript
// Current (Working)
<ThreadProvider>
  <TaskProvider>
    <StreamProvider>

// Alternative (Simpler)
<DataProvider> // Merged Task + Thread
  <StreamProvider>
```

### 🏗️ Future Enhancements

#### 1. Enhanced Real-time Features
- WebSocket integration for instant updates
- Optimistic UI updates
- Real-time collaboration indicators

#### 2. Performance Optimization
- Virtual scrolling for large task lists
- React Query for sophisticated caching
- Memoization of expensive computations

#### 3. User Experience Improvements
- Task filtering and search
- Bulk task operations
- Enhanced error recovery

## Provider Integration & Dependencies

### Current Provider Nesting
```typescript
// apps/web/src/app/page.tsx
<ThreadProvider>      // ✅ Active - Thread metadata
  <TaskProvider>      // ✅ Active - Task data + real-time updates  
    <StreamProvider>  // ✅ Active - Real-time communication
      <ArtifactProvider>
        <Thread />
      </ArtifactProvider>
    </StreamProvider>
  </TaskProvider>
</ThreadProvider>
```

### Integration Points
1. **TaskProvider ↔ StreamProvider**: Active thread tracking integration
2. **TaskProvider ↔ ThreadProvider**: Minimal overlap, mostly independent
3. **All Providers**: Share common LangGraph API client configuration

## Conclusion

**Key Strengths**:
- ✅ Real-time status updates with polling
- ✅ Simplified and debuggable task IDs  
- ✅ Advanced status inference logic
- ✅ Robust error handling and API resilience
- ✅ Unified thread rendering with variants

**Areas for Consideration**:
- 🔄 Task component usage strategy
- 🔄 Provider architecture simplification
- 🔄 Multiple task rendering pattern consolidation
