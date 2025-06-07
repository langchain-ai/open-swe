# Plan Creation & Display System Reference

## Overview

The Open-SWE project uses a sophisticated plan-based AI agent system where the LangGraph agent creates detailed execution plans before taking actions. This document explains the current plan creation system, how plans are displayed in the UI using the implemented **PlanViewer** component, and provides a roadmap for future enhancements.

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

## Current Implementation Status

### ✅ Implemented Features

#### 1. Visual Plan Display Component (`apps/web/src/components/plan/plan-viewer.tsx`)

**Status**: ✅ **COMPLETED and INTEGRATED**

```typescript
interface PlanViewerProps {
  planItems: PlanItem[];
  className?: string;
}

export function PlanViewer({ planItems, className }: PlanViewerProps) {
  // Implemented features:
  // - Visual task list with status indicators
  // - Automatic current task detection
  // - Progress counter (X of Y completed)
  // - Status-based styling and icons
  // - Task summaries for completed items
}
```

**Key Features**:
- **Status Icons**: Check (completed), Play (current), Clock (remaining)
- **Status-based Styling**: Green (completed), Blue (current), Gray (remaining)
- **Progress Tracking**: Shows "X of Y completed" header
- **Current Task Detection**: Automatically finds lowest index where `completed: false`
- **Task Summaries**: Displays summaries for completed tasks in styled containers
- **Responsive Design**: Proper mobile layout with card-based design

#### 2. Interrupt System Integration (`apps/web/src/components/thread/agent-inbox/components/inbox-item-input.tsx`)

**Status**: ✅ **COMPLETED**

The PlanViewer is **fully integrated** into the interrupt system:

```typescript
// Automatically detects plan data and renders PlanViewer
function ArgsRenderer({ args }: { args: Record<string, any> }) {
  const planItems = parsePlanData(args);
  
  if (planItems.length > 0) {
    return (
      <div className="w-full max-w-full rounded-xl border border-gray-200 bg-white p-4">
        <PlanViewer planItems={planItems} />
      </div>
    );
  }
  
  // Fallback to markdown for non-plan data
}
```

**Integration Points**:
- **Automatic Detection**: Uses `isPlanData()` to identify plan content
- **Data Parsing**: `parsePlanData()` handles both string and structured formats
- **Seamless Replacement**: Replaces old markdown display for plan interrupts
- **Fallback Support**: Maintains backward compatibility with non-plan interrupts

#### 3. Plan Data Utilities (`apps/web/src/lib/plan-utils.ts`)

**Status**: ✅ **COMPLETED**

```typescript
// Smart plan detection
export function isPlanData(args: Record<string, any>): boolean;

// Flexible plan parsing (supports both formats)
export function parsePlanData(args: Record<string, any>): PlanItem[];

// Key identification for debugging
export function getPlanKey(args: Record<string, any>): string | null;
```

**Capabilities**:
- **Dual Format Support**: Handles both `:::` separated strings and structured `PlanItem[]`
- **Smart Detection**: Identifies plan data by content patterns
- **Future-ready**: Prepared for backend to send structured data
- **Robust Parsing**: Handles malformed or partial plan data gracefully

### 🔄 Current User Experience Flow

#### Plan Presentation & Interaction
1. **Plan Generation**: Agent generates plan via `interrupt-plan.ts`
2. **Visual Display**: PlanViewer shows plan with clear visual hierarchy
3. **User Actions**: Standard interrupt options (Accept/Edit/Respond/Ignore)
4. **Status Tracking**: Real-time visual feedback as plan executes

#### What Users See Now
- **Clean Visual Interface**: No more plain text with `:::` separators
- **Clear Progress**: Visual progress indicators and completion status
- **Structured Layout**: Card-based design with proper spacing and icons
- **Task Summaries**: Dedicated sections for completed task summaries
- **Responsive Design**: Works well on all screen sizes

## Data Flow & Integration

### Current Architecture
```
Plan Generation → interrupt-plan.ts → HumanInterrupt → 
inbox-item-input.tsx → parsePlanData() → PlanViewer → Visual UI
```

### Status Detection Logic
```typescript
const getTaskStatus = (item: PlanItem) => {
  if (item.completed) return "completed";
  if (item.index === currentTaskIndex) return "current";  
  return "remaining";
};
```

### Visual Status Mapping
- **Completed Tasks** (`completed: true`): Green background, check icon, shows summary
- **Current Task** (lowest incomplete index): Blue background, play icon, "In Progress" badge  
- **Remaining Tasks** (future tasks): Gray background, clock icon, muted text

## Outstanding Limitations & Future Opportunities

### 📋 Current Constraints

#### 1. Read-only Display
**Current State**: PlanViewer is display-only
**Missing**: Interactive editing, reordering, adding/removing tasks
**Impact**: Users must edit plans as text in edit mode

#### 2. Basic Status Detection  
**Current State**: Simple completed/current/remaining logic
**Missing**: Error states, progress percentages, estimated durations
**Impact**: Limited visibility into execution problems

#### 3. Single Plan Version
**Current State**: No plan history or versioning
**Missing**: Version comparison, change tracking, revert capability
**Impact**: Can't see plan evolution or recover from mistakes

#### 4. No Real-time Updates
**Current State**: Static display based on interrupt data
**Missing**: Live status updates during execution
**Impact**: Users don't see progress in real-time

#### 5. Limited Plan Metadata
**Current State**: Basic task description and completion
**Missing**: Time estimates, dependencies, categories, priorities
**Impact**: Reduced planning and tracking capabilities

## Recommended Roadmap

### 🎯 Phase 1: Enhanced Status & Real-time Updates (Next Sprint)

#### 1. Real-time Status Integration
**Goal**: Connect PlanViewer to live execution state
```typescript
// Integrate with task system status inference
interface PlanItemWithLiveStatus extends PlanItem {
  status: "running" | "interrupted" | "done" | "error";
  progress?: number;        // 0-100 completion percentage
  errorMessage?: string;    // Error details if failed
}
```

#### 2. Enhanced Status Indicators
**Add**:
- **Loading States**: Spinner for running tasks
- **Error Indicators**: Warning icons and error messages
- **Progress Bars**: For long-running tasks
- **Time Estimates**: Expected completion times

#### 3. Stream Integration
**Connect**: PlanViewer with `useStreamContext()` for live updates
**Benefit**: Real-time status changes during execution

### 🏗️ Phase 2: Interactive Editing (Month 2)

#### 1. Inline Task Editing
**Goal**: Edit individual task descriptions directly in PlanViewer
```typescript
interface EditablePlanViewerProps extends PlanViewerProps {
  editable?: boolean;
  onTaskEdit?: (index: number, newText: string) => void;
  onTaskDelete?: (index: number) => void;
  onTaskAdd?: (afterIndex: number, text: string) => void;
}
```

#### 2. Drag & Drop Reordering
**Add**: Drag handles for incomplete tasks
**Library**: `@dnd-kit/core` for robust drag-and-drop
**Constraint**: Only allow reordering of non-started tasks

#### 3. Task Management Actions
**Features**:
- Add task buttons between existing tasks
- Delete task confirmations
- Task splitting (break large tasks into smaller ones)
- Task merging (combine similar tasks)

### 🎨 Phase 3: Advanced Features (Month 3)

#### 1. Plan Versioning & History
**Goal**: Track plan changes over time
```typescript
interface PlanVersion {
  id: string;
  version: number;
  items: PlanItem[];
  timestamp: Date;
  changeDescription?: string;
}
```

#### 2. Plan Templates & Patterns
**Features**:
- Save common plan patterns
- Template library for frequent tasks
- Smart plan suggestions based on repository type

#### 3. Enhanced Metadata & Organization
**Add**:
- Task categories/tags
- Priority levels
- Time estimates
- Task dependencies
- Resource requirements

### 🚀 Phase 4: Advanced Collaboration (Month 4)

#### 1. Multi-user Plan Collaboration
**Features**:
- Shared plan editing
- Real-time collaboration cursors
- Comment system on tasks
- Plan approval workflows

#### 2. Plan Analytics & Insights
**Add**:
- Completion time tracking
- Success rate analytics
- Common failure points
- Performance optimization suggestions

## Technical Implementation Notes

### Current Component Structure
```
apps/web/src/components/plan/
├── index.ts              # ✅ Exports PlanViewer
├── plan-viewer.tsx       # ✅ Main visual component
└── plan-utils.ts         # ✅ (in lib/) Data parsing utilities
```

### Integration Architecture
```typescript
// Current working integration
inbox-item-input.tsx
├── ArgsRenderer (detects plan data)
├── parsePlanData (converts to PlanItem[])
└── PlanViewer (renders visual plan)

// Future enhanced integration  
├── PlanViewerWithStreaming (real-time updates)
├── EditablePlanViewer (interactive editing)
└── PlanHistoryViewer (version management)
```

### Performance Considerations
- **Current**: Handles 20-50 tasks efficiently
- **Optimization Target**: Support 100+ tasks with virtual scrolling
- **Memory**: Minimal re-renders with proper memoization
- **Network**: Debounced updates for real-time features

## Success Metrics & Validation

### Current Achievement ✅
- **User Experience**: Plan display moved from plain text to rich visual interface
- **Developer Experience**: Clear component separation and reusable utilities
- **Integration**: Seamless replacement of markdown display in interrupt system
- **Compatibility**: Supports both current string format and future structured data

### Next Phase Targets 🎯
- **Real-time Updates**: <100ms latency for status changes
- **Edit Performance**: <50ms response time for interactive edits
- **User Adoption**: 90% of users prefer visual plan over text editing
- **Error Reduction**: 70% fewer plan editing mistakes with visual interface

## Migration & Deployment Strategy

### ✅ Completed Migration
- **Phase 1**: PlanViewer component created and tested
- **Phase 2**: Integration with interrupt system completed
- **Phase 3**: Plan parsing utilities implemented
- **Phase 4**: Full replacement of markdown plan display

### Future Deployment Plan
- **Feature Flags**: Roll out advanced features gradually
- **A/B Testing**: Compare enhanced features with current implementation
- **Backward Compatibility**: Maintain support for text-based editing
- **Progressive Enhancement**: Add features without breaking existing workflows

## Conclusion

The plan system has successfully evolved from a text-based display to a rich visual interface with the **PlanViewer** component. The current implementation provides a solid foundation with:

**✅ Completed Achievements**:
- **Visual Plan Display**: Rich interface with status indicators and progress tracking
- **Seamless Integration**: Fully integrated into the interrupt system
- **Flexible Data Handling**: Supports both current and future plan formats
- **Enhanced User Experience**: Clear visual hierarchy and responsive design
- **Developer-Friendly**: Reusable component with clean API

**🎯 Next Steps**:
- **Real-time Updates**: Connect to live execution status
- **Interactive Editing**: Enable direct plan manipulation in visual interface
- **Advanced Features**: Plan versioning, templates, and collaboration tools

The foundation is strong and ready for the next phase of enhancements. The current implementation significantly improves the user experience while maintaining full compatibility with the existing LangGraph-based execution system. 