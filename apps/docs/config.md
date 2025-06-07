# Configuration Management Reference

## Overview

The Open-SWE project uses a sophisticated configuration system that allows users to customize the behavior of the LangGraph-powered AI agent through a configuration sidebar. This document explains how the configuration system works, its integration with the LangGraph backend, and specific use cases for model configuration.

## Architecture Overview

### Core Components

1. **Configuration Sidebar** (`apps/web/src/components/configuration-sidebar/`)
   - `index.tsx`: Main sidebar component with tabs and field organization
   - `config-field.tsx`: Individual field components (selects, sliders, inputs, etc.)
   - `config-section.tsx`: Section grouping component

2. **State Management** (`apps/web/src/hooks/use-config-store.tsx`)
   - Zustand store with localStorage persistence
   - Nested structure: `configs[fieldName][fieldName] = value`
   - Handles defaults, updates, and resets

3. **Type Definitions** 
   - `apps/web/src/types/configurable.ts`: UI metadata types
   - `apps/open-swe/src/types.ts`: LangGraph configuration schema

4. **Integration Point** (`apps/web/src/components/thread/index.tsx`)
   - `getActualConfigs()`: Extracts config values for LangGraph
   - Passes config through `stream.submit()` call

## Configuration Schema

The configuration is defined by the `GraphConfiguration` Zod schema in `apps/open-swe/src/types.ts`:

### Model Configuration Fields

| Field | Type | Purpose | Options |
|-------|------|---------|---------|
| `plannerModelName` | select | Planning & rewriting | No thinking models |
| `plannerContextModelName` | select | Context gathering | All models |
| `actionGeneratorModelName` | select | Code generation | All models |
| `progressPlanCheckerModelName` | select | Progress validation | No thinking models |
| `summarizerModelName` | select | History summarization | No thinking models |

### Available Models

**Anthropic Models:**
- Claude Sonnet 4 (Extended Thinking)
- Claude Opus 4 (Extended Thinking) 
- Claude Sonnet 4
- Claude Opus 4
- Claude 3.7 Sonnet
- Claude 3.5 Sonnet

**OpenAI Models:**
- o4, o4 mini, o3, o3 mini
- GPT 4o, GPT 4.1

**Google Models:**
- Gemini 2.5 Pro Preview
- Gemini 2.5 Flash Preview

### Other Configuration Fields

- `plannerTemperature`, `actionGeneratorTemperature`, etc.: Control randomness (0-2)
- `maxContextActions`: Limit context gathering steps (1-20)
- `maxTokens`: Token generation limit (1-64,000)
- Hidden fields: GitHub tokens

## Data Flow

### 1. Configuration Storage
```
User changes field → ConfigField component → useConfigStore → localStorage
```

### 2. Configuration Retrieval
```
User submits message → getActualConfigs() → Extract nested values → stream.submit()
```

### 3. LangGraph Integration
```
stream.submit({ config: { configurable: {...configs} } }) → LangGraph nodes → loadModel()
```

## Use Cases & Behavior

### Case 1: Changing Model Before Starting New Thread

**Current Behavior:**
- User opens configuration sidebar
- Changes `plannerModelName` from Claude Sonnet 4 to GPT 4o
- Starts new conversation
- ✅ **New thread uses GPT 4o for all planning steps**

**Data Flow:**
1. Config change saved to localStorage via useConfigStore
2. New thread submission includes updated config
3. All subsequent LangGraph nodes use new model

### Case 2: Changing Model After Thread Started

**Current Behavior:**
- User has active thread with Claude Sonnet 4
- Changes config to GPT 4o mid-conversation
- Sends next message
- ✅ **Next message and all subsequent actions use GPT 4o**

**Important Notes:**
- Configuration is passed with **every message submission**
- No restart required - changes take effect immediately
- Previous messages in thread history remain unchanged

### Case 3: OpenAI Model Limitations

**Current Issues:**
- ⚠️ **Anthropic-specific code patterns in some nodes**
- Some prompt templates may be optimized for Claude
- Error handling may not account for OpenAI-specific responses

**Evidence:**
- Hard-coded Anthropic patterns in planning prompts
- Temperature settings ignored for reasoning models
- Tool usage patterns may differ between providers

## Technical Implementation Details

### Configuration Store Structure
```typescript
configs: {
  plannerModelName: {
    plannerModelName: "anthropic:claude-sonnet-4-0",
    __defaultValues: { plannerModelName: "anthropic:claude-sonnet-4-0" }
  },
  // ... other fields
}
```

### Integration with LangGraph
```typescript
// In thread/index.tsx
const getActualConfigs = () => {
  const actualConfigs: Record<string, any> = {};
  Object.entries(configs).forEach(([key, configObj]) => {
    if (configObj && typeof configObj === "object" && configObj[key] !== undefined) {
      actualConfigs[key] = configObj[key];
    }
  });
  return actualConfigs;
};

// Passed to LangGraph
stream.submit(input, {
  config: {
    recursion_limit: 400,
    configurable: { ...getActualConfigs() }
  }
});
```

### Model Loading in LangGraph
```typescript
// In open-swe/src/utils/load-model.ts
export function loadModel(task: Task, config: GraphConfig) {
  const modelName = getModelNameForTask(task, config);
  return initChatModel(modelName, { temperature, maxTokens });
}
```

## Current Issues & Limitations

### 1. OpenAI Model Support
- **Issue**: Code assumes Anthropic-specific behaviors
- **Impact**: OpenAI models may not perform optimally
- **Evidence**: Hardcoded Anthropic patterns in prompts

### 2. Configuration UI Inconsistencies
- **Issue**: Some fields show "no thinking models" while others show all
- **Impact**: User confusion about which models work where
- **Current**: Inconsistent filtering based on field purpose

### 3. Real-time Feedback
- **Issue**: No indication when config changes take effect
- **Impact**: Users unsure if changes applied
- **Missing**: Visual feedback for active configuration

### 4. Configuration Validation
- **Issue**: No validation of compatible model combinations
- **Impact**: Users can select incompatible configurations
- **Missing**: Cross-field validation and warnings

## Proposed Improvements

### 1. Enhanced Model Compatibility

**Priority: High**

**Changes:**
- Add provider-aware prompt templates
- Implement model-specific error handling
- Test all supported models thoroughly
- Add compatibility matrix documentation

**Implementation:**
```typescript
// Proposed: Provider-aware model loading
const getProviderOptimizedPrompt = (basePrompt: string, provider: string) => {
  switch (provider) {
    case 'anthropic': return optimizeForClaude(basePrompt);
    case 'openai': return optimizeForGPT(basePrompt);
    case 'google-genai': return optimizeForGemini(basePrompt);
    default: return basePrompt;
  }
};
```

### 2. Unified Model Management

**Priority: High**

**Changes:**
- Single "Primary Model" selector that updates all fields
- Advanced mode for per-task model selection
- Clear indication of which models are used where
- Automatic fallback for incompatible combinations

**UI Mockup:**
```
┌─ Model Configuration ─────────────────┐
│ Primary Model: [Claude Sonnet 4    ▼] │
│ ☐ Advanced: Configure per task        │
│                                       │
│ ✓ Planning: Claude Sonnet 4          │
│ ✓ Code Generation: Claude Sonnet 4   │
│ ✓ Progress Checking: Claude Sonnet 4 │
└───────────────────────────────────────┘
```

### 3. Real-time Configuration Status

**Priority: Medium**

**Changes:**
- Show active configuration in thread header
- Highlight when config changes take effect
- Display model usage per message
- Add configuration diff indicator

**Implementation:**
```typescript
// Show active config in thread
<ThreadHeader>
  <ModelIndicator model={activeConfig.plannerModelName} />
  {configChanged && <ConfigChangedBadge />}
</ThreadHeader>
```

### 4. Configuration Presets

**Priority: Medium**

**Changes:**
- Predefined configurations (Fast, Balanced, Powerful)
- Save/load custom presets
- Import/export configurations
- Team-shared configurations

**Presets:**
- **Fast**: Claude 3.5 Sonnet, low tokens, minimal context
- **Balanced**: Claude Sonnet 4, standard settings
- **Powerful**: Claude Opus 4, high tokens, extended context

### 5. Enhanced Validation & Feedback

**Priority: Medium**

**Changes:**
- Real-time validation of model combinations
- Warning for potentially expensive configurations
- Estimated cost/speed indicators
- Performance recommendations

**Validation Examples:**
```typescript
const validateConfig = (config: GraphConfig) => {
  const warnings = [];
  
  if (config.maxTokens > 32000) {
    warnings.push("High token limit may increase response time");
  }
  
  if (isExpensiveModel(config.plannerModelName)) {
    warnings.push("Premium model selected - costs may be higher");
  }
  
  return warnings;
};
```

### 6. Configuration Analytics

**Priority: Low**

**Changes:**
- Track configuration usage patterns
- Performance metrics per configuration
- Recommendations based on usage
- A/B testing for optimal configurations

## Migration Plan

### Phase 1: Foundation (Week 1-2)
1. Fix OpenAI model compatibility issues
2. Add provider-aware prompt templates
3. Implement unified model selector
4. Add basic validation

### Phase 2: Enhanced UX (Week 3-4)
1. Add configuration presets
2. Implement real-time status indicators
3. Add configuration validation
4. Improve error handling

### Phase 3: Advanced Features (Week 5-6)
1. Add configuration analytics
2. Implement team configurations
3. Add performance recommendations
4. Enhanced testing and documentation

## Development Guidelines

### Adding New Configuration Fields

1. **Update Schema** (`apps/open-swe/src/types.ts`):
```typescript
newField: z.string().optional().langgraph.metadata({
  x_oap_ui_config: {
    type: "select",
    default: "default-value",
    description: "Field description",
    options: [...],
  },
}),
```

2. **Update UI** (`apps/web/src/components/configuration-sidebar/index.tsx`):
```typescript
{
  label: "newField",
  type: "select",
  description: "Field description",
  options: [...],
  default: "default-value",
}
```

3. **Update Backend** (`apps/open-swe/src/utils/load-model.ts`):
```typescript
const newFieldValue = config.configurable?.newField ?? "default-value";
```

### Testing Configuration Changes

1. **Unit Tests**: Test configuration store operations
2. **Integration Tests**: Test config → LangGraph integration
3. **E2E Tests**: Test full user workflow
4. **Performance Tests**: Measure impact of different configurations

## Conclusion

The configuration system is well-architected but needs improvements for better OpenAI support, enhanced UX, and real-time feedback. The proposed changes will make the system more robust, user-friendly, and maintainable while preserving the existing functionality that works well. 