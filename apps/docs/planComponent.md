# Plan Creation & Display System Reference

## Overview

The Open-SWE project uses a sophisticated plan-based AI agent system where the LangGraph agent creates detailed execution plans before taking actions. This document explains the current plan creation system, how plans are displayed in the UI, and prepares for implementing an improved visual plan component.

## Architecture Overview

### Core Components

#### 1. Plan Data Structure (`apps/open-swe/src/types.ts` & `apps/web/src/types/index.ts`)

```typescript
export type PlanItem = {
  index: number;           // Order of execution (0-based)
  plan: string;           // Task description
  completed: boolean;     // Completion status
  summary?: string;       // Summary of completed work (optional)
};
```

#### 2. Plan Generation Pipeline

**Location**: `apps/open-swe/src/subgraphs/planner/`

1. **Context Gathering** (`generate-message.ts`)
   - Agent gathers repository context (max 6 tool calls)
   - Uses `rg` for fast file searches
   - Read-only operations to understand codebase

2. **Plan Creation** (`generate-plan.ts`)
   - Calls `sessionPlanTool` to generate high-level tasks
   - Creates `proposedPlan: string[]` array
   - Optimizes for minimal steps while being actionable

3. **Plan Interruption** (`apps/open-swe/src/nodes/interrupt-plan.ts`)
   - Presents plan to user for approval/editing
   - Converts `proposedPlan` to `PlanItem[]` array
   - Supports accept/edit/respond/ignore actions

#### 3. Plan State Management

**Location**: `apps/open-swe/src/types.ts` - `GraphState` interface

```typescript
interface GraphState {
  plan: PlanItem[];           // Active execution plan
  proposedPlan: string[];     // Generated plan pending approval
  planChangeRequest?: string; // User feedback for plan changes
}
```

#### 4. Plan Execution & Progress Tracking

- **Current Task Detection**: First non-completed task in plan
- **Progress Updates**: `apps/open-swe/src/nodes/progress-plan-step.ts`
- **Task Completion**: Adds summary when task finished
- **Plan Rewriting**: `apps/open-swe/src/nodes/rewrite-plan.ts` for user feedback

## Current UI Implementation

### Plan Display Location

**Primary Component**: `apps/web/src/components/thread/agent-inbox/components/inbox-item-input.tsx`

Plans are currently displayed through the **agent interrupt system**:

1. **Interrupt Trigger**: When plan is generated (`interrupt-plan.ts`)
2. **Display Method**: `ArgsRenderer` component shows plan as markdown
3. **Interaction**: Plan text appears in editable textarea
4. **Format**: Plan steps separated by `":::"` delimiter

### Current Display Flow

```
Plan Generation → interrupt-plan.ts → HumanInterrupt → 
inbox-item-input.tsx → ArgsRenderer → MarkdownText
```

### Current UI Components

#### ArgsRenderer Component
```typescript
function ArgsRenderer({ args }: { args: Record<string, any> }) {
  return (
    <div className="flex w-full flex-col items-start gap-6">
      {Object.entries(args).map(([k, v]) => {
        // Displays plan as formatted markdown text
        return (
          <span className="w-full max-w-full rounded-xl bg-zinc-100 p-3">
            <MarkdownText>{value}</MarkdownText>
          </span>
        );
      })}
    </div>
  );
}
```

#### EditAndOrAcceptComponent
- **Purpose**: Allows plan editing in large textarea
- **Format**: Plain text with `:::` separators
- **Limitations**: No visual task states, no drag-and-drop, basic text editing

### Current User Interaction Flow

1. **Plan Presentation**: Plan appears in interrupt dialog
2. **User Options**:
   - **Accept**: Approve plan as-is
   - **Edit**: Modify plan text (textarea with `:::` separators)
   - **Respond**: Provide feedback for AI to rewrite plan
   - **Ignore**: Dismiss plan

3. **Plan Processing**:
   - **Accept/Edit**: Converts to `PlanItem[]` and begins execution
   - **Respond**: Triggers plan rewriting with user feedback

## Plan States & Status System

### Task Status Detection (`apps/web/src/lib/thread-utils.ts`)

```typescript
type TaskStatus = "done" | "running" | "interrupted" | "error";

function inferTaskStatus(task: PlanItem, taskIndex: number, threadValues: any): TaskStatus {
  if (task.completed) return "done";
  
  const currentTaskIndex = getCurrentTaskIndex(plan);
  const isCurrentTask = taskIndex === currentTaskIndex;
  
  if (isCurrentTask) {
    if (hasError) return "error";
    if (hasInterrupt) return "interrupted"; 
    if (isActive) return "running";
  }
  
  return taskIndex < currentTaskIndex ? "error" : "interrupted";
}
```

### Three Primary States

1. **Completed Tasks** (`completed: true`)
   - Shows green checkmark or completion indicator
   - Display task summary if available
   - Read-only state

2. **Current Task** (lowest index where `completed: false`)
   - Shows running/interrupted/error status
   - Active visual indicator
   - May show progress or error details

3. **Remaining Tasks** (future tasks, `completed: false`)
   - Shows as pending/queued
   - Grayed out or secondary styling
   - Available for editing/reordering

## Current Limitations & Pain Points

### 1. Poor Visual Representation
- **Issue**: Plans displayed as plain markdown text
- **Impact**: No visual hierarchy, status indicators, or progress tracking
- **User Experience**: Difficult to scan, understand progress, or track status

### 2. Limited Editing Capabilities
- **Issue**: Text-based editing with `:::` separators
- **Impact**: Error-prone, no validation, no task reordering
- **Missing Features**: Drag-and-drop, individual task editing, task insertion

### 3. No Real-time Progress Visualization
- **Issue**: No visual feedback during plan execution
- **Impact**: Users can't see which task is active or track progress
- **Missing**: Progress bars, status icons, execution timeline

### 4. Single Plan Version
- **Issue**: No history of plan changes or versions
- **Impact**: Can't see how plan evolved or revert changes
- **Missing**: Plan versioning, change tracking, revision history

### 5. No Multi-Request Support
- **Issue**: Each new request overwrites previous plan
- **Impact**: Can't manage multiple concurrent plans
- **Missing**: Plan grouping, request correlation, plan archives

## Proposed Plan Component Architecture

### Visual Design Inspiration
Based on v0 design: https://v0.dev/chat/CpZeN9MiCic

### New Component Structure

```
apps/web/src/components/plan/
├── index.tsx              # Main PlanViewer component
├── plan-item.tsx          # Individual task display
├── plan-editor.tsx        # Edit mode with drag-and-drop
├── plan-history.tsx       # Version navigation
├── plan-status.tsx        # Progress indicators
└── types.ts              # Component-specific types
```

### Enhanced Data Model

```typescript
// Extended plan structure for multiple versions
interface PlanVersion {
  id: string;              // Unique version ID
  index: number;           // Version number (0, 1, 2...)
  items: PlanItem[];       // Plan tasks
  createdAt: Date;         // Timestamp
  description?: string;    // Change description
}

interface PlanCollection {
  requestId: string;       // Parent request ID
  threadId: string;        // Associated thread
  versions: PlanVersion[]; // Plan evolution
  activeVersionId: string; // Currently active version
}

// Enhanced task status
interface PlanItemWithStatus extends PlanItem {
  status: TaskStatus;
  progress?: number;       // 0-100 completion percentage
  errorMessage?: string;   // Error details if failed
  estimatedDuration?: string; // Time estimate
}
```

### Key Features for New Component

#### 1. Visual Task List
- **Checkboxes**: Clear completion status
- **Status Icons**: Running (spinner), error (warning), pending (clock)
- **Progress Bars**: For long-running tasks
- **Collapsible Details**: Show/hide task summaries

#### 2. Interactive Editing
- **Drag & Drop**: Reorder incomplete tasks
- **Inline Editing**: Click to edit individual tasks
- **Add Tasks**: Insert new tasks at any position
- **Delete Tasks**: Remove tasks (if not started)

#### 3. Plan Versioning
- **Version Timeline**: Navigate through plan iterations
- **Diff View**: Compare plan versions
- **Revert Capability**: Roll back to previous versions

#### 4. Multi-Request Management
- **Request Grouping**: Organize plans by user requests
- **Plan Archives**: Access completed plan history
- **Search & Filter**: Find specific plans or tasks

#### 5. Real-time Updates
- **Live Status**: Update task status as execution progresses
- **Streaming Progress**: Show real-time task progression
- **Error Feedback**: Immediate error display with context

## Integration Points with Current System

### 1. Interrupt System Integration
- **Replace**: Current `ArgsRenderer` plan display
- **Enhance**: `EditAndOrAcceptComponent` with visual editor
- **Maintain**: Same accept/edit/respond/ignore workflow

### 2. State Management Integration
- **Connect**: `useStreamContext` for real-time updates
- **Extend**: Plan state in thread values
- **Add**: Plan history persistence

### 3. Backend Integration Points

#### Plan Creation
```typescript
// In interrupt-plan.ts - modify plan presentation
const interruptRes = interrupt<HumanInterrupt, HumanResponse[]>({
  action_request: {
    action: "Approve/Edit Plan",
    args: {
      planItems: proposedPlan.map((plan, index) => ({
        index,
        plan,
        completed: false
      })), // Send structured data instead of string
    },
  },
  // ... rest of config
});
```

#### Plan Updates
```typescript
// Enhanced plan progress tracking
export async function progressPlanStep(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  // Add progress percentage and status updates
  return {
    plan: state.plan.map(task => 
      task.index === currentTaskIndex 
        ? { ...task, status: "running", progress: estimatedProgress }
        : task
    )
  };
}
```

### 4. UI Component Integration
```typescript
// Replace current interrupt display
function InboxItemInput({ interruptValue, ... }) {
  if (interruptValue.action_request.action === "Approve/Edit Plan") {
    return <PlanViewer planItems={interruptValue.action_request.args.planItems} />;
  }
  
  // ... existing logic for other interrupts
}
```

## Implementation Phases

### Phase 1: Basic Visual Plan Display (Week 1)
1. **Create PlanViewer component** with task list UI
2. **Add status icons** for completed/current/remaining states
3. **Replace markdown display** in inbox-item-input.tsx
4. **Basic styling** following v0 design patterns

### Phase 2: Interactive Editing (Week 2)
1. **Implement drag-and-drop** for task reordering
2. **Add inline editing** for individual tasks
3. **Task insertion/deletion** capabilities
4. **Form validation** and error handling

### Phase 3: Real-time Updates (Week 3)
1. **Connect to streaming context** for live updates
2. **Progress indicators** and status animations
3. **Error state display** with contextual information
4. **Performance optimization** for frequent updates

### Phase 4: Advanced Features (Week 4)
1. **Plan versioning** and history navigation
2. **Multi-request support** and plan collections
3. **Search and filtering** capabilities
4. **Export/import** functionality

## Technical Considerations

### Performance
- **Virtual scrolling** for large plans (100+ tasks)
- **Debounced updates** for real-time status changes
- **Memoization** of plan items to prevent unnecessary re-renders

### Accessibility
- **Keyboard navigation** for all interactions
- **Screen reader support** with proper ARIA labels
- **Focus management** during drag operations

### Mobile Responsiveness
- **Touch-friendly** drag handles
- **Responsive layout** for different screen sizes
- **Optimized gestures** for mobile editing

### Data Persistence
- **Local storage** for draft plan edits
- **Auto-save** functionality during editing
- **Conflict resolution** for concurrent edits

## Success Metrics

### User Experience
- **Reduced plan editing time** by 60%
- **Increased plan approval rate** (less back-and-forth)
- **Improved task completion visibility**

### Development Efficiency
- **Faster debugging** with visual plan state
- **Better error tracking** at task level
- **Enhanced collaboration** with shared plan views

### System Performance
- **Real-time updates** under 100ms latency
- **Smooth animations** at 60fps
- **Minimal re-renders** (<5 per status update)

## Migration Strategy

### Backward Compatibility
- **Gradual rollout** with feature flags
- **Fallback mode** to current text-based editing
- **A/B testing** for user preference validation

### Data Migration
- **Convert existing plans** to new format
- **Preserve plan history** where available
- **Handle legacy formats** gracefully

## Conclusion

The current plan system provides a solid foundation but lacks the visual richness and interactivity needed for optimal user experience. The proposed plan component will transform plan management from a text-based workflow to an intuitive, visual interface that supports complex editing, real-time updates, and multi-version tracking.

Key benefits of the new system:
- **Enhanced Visibility**: Clear visual representation of plan progress
- **Improved Editing**: Drag-and-drop reordering and inline editing
- **Better Tracking**: Real-time status updates and error visibility
- **Version Control**: Plan history and change management
- **Scalability**: Support for multiple plans and large task lists

This upgrade will significantly improve the user experience while maintaining compatibility with the existing LangGraph-based execution system. 