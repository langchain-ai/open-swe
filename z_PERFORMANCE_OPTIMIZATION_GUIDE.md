# 🚀 Open SWE Performance Optimization Guide

**Focus:** Speed up NVIDIA LLM Gateway responses

---

## 📊 **Current Performance Baseline**

From your logs and our tests:

| Operation | Time | Status |
|-----------|------|--------|
| Starfleet token (first) | ~680ms | ✅ Fast |
| Starfleet token (cached) | <1ms | ✅ Excellent |
| Simple chat (gpt-4o-mini) | ~1.4s | ✅ Good |
| Tool calling (gpt-4o-mini) | ~2.0s | ✅ Acceptable |
| Complex planning (gpt-4o) | ~3-5s | ⚠️ Can improve |

---

## ⚡ **Quick Wins (Already Implemented)**

✅ **1. Streaming Enabled**
- Added `streaming: true` to ChatOpenAI config
- Faster perceived performance
- User sees tokens as they arrive

✅ **2. Token Caching**
- Starfleet tokens cached for 15 minutes
- Saves ~680ms on every request after the first

✅ **3. Timeout Set**
- 60-second timeout prevents hanging
- Fails fast if Gateway has issues

---

## 🎯 **Additional Optimizations to Try**

### **1. Reduce Max Tokens (If Appropriate)**

**Current:** ~10,000 tokens max

**Optimize:** Set lower for simple tasks

**File:** `apps/open-swe/src/utils/llms/model-manager.ts`

```typescript
// Before
let finalMaxTokens = maxTokens ?? 10_000;

// After (for faster responses)
let finalMaxTokens = maxTokens ?? 4_000; // Reduce for faster generation
```

**Trade-off:**
- ✅ Faster responses
- ❌ May truncate long responses

---

### **2. Use gpt-4o-mini for More Tasks**

**Current:** Using gpt-4o for Planner and Programmer

**Optimize:** Use gpt-4o-mini for everything

**File:** `packages/shared/src/open-swe/llm-task.ts`

```typescript
export const TASK_TO_CONFIG_DEFAULTS_MAP = {
  [LLMTask.PLANNER]: {
    modelName: "nvidia-gateway:gpt-4o-mini", // Changed from gpt-4o
    temperature: 0,
  },
  [LLMTask.PROGRAMMER]: {
    modelName: "nvidia-gateway:gpt-4o-mini", // Changed from gpt-4o
    temperature: 0,
  },
  // Rest stays gpt-4o-mini
};
```

**Trade-off:**
- ✅ 2-3x faster responses
- ✅ Lower cost
- ❌ Slightly lower code quality

---

### **3. Enable Parallel Tool Execution**

Check if parallel tool calls are enabled for faster multi-tool tasks.

**File:** Look for `supportsParallelToolCallsParam` usage

---

### **4. Reduce Context Size**

**Current:** Sending full conversation history

**Optimize:** Summarize or truncate old messages

This would require code changes to truncate context before sending to LLM.

---

### **5. Add Request Queuing/Batching**

If multiple agents are running, queue requests to avoid overwhelming the Gateway.

---

## 🔧 **Quick Configuration Changes**

### **Option A: Fast but Less Quality (gpt-4o-mini everywhere)**

Edit `.env`:
```bash
LLM_GATEWAY_MODEL=gpt-4o-mini
```

Edit `packages/shared/src/open-swe/llm-task.ts`:
```typescript
// Use gpt-4o-mini for everything
[LLMTask.PLANNER]: {
  modelName: "nvidia-gateway:gpt-4o-mini",
  temperature: 0,
},
[LLMTask.PROGRAMMER]: {
  modelName: "nvidia-gateway:gpt-4o-mini",
  temperature: 0,
},
```

**Result:**
- 2-3x faster
- Lower cost
- Slightly lower quality

---

### **Option B: Balanced (Current Setup)**

```typescript
// Keep gpt-4o for complex tasks
PLANNER: gpt-4o       (complex planning)
PROGRAMMER: gpt-4o    (code generation)

// Use gpt-4o-mini for simple tasks
REVIEWER: gpt-4o-mini    (fast reviews)
ROUTER: gpt-4o-mini      (fast routing)
SUMMARIZER: gpt-4o-mini  (fast summaries)
```

**Result:**
- Good balance of speed and quality
- Current configuration ✅

---

### **Option C: Quality First (Slower but Better)**

```typescript
// Use gpt-4o for everything critical
PLANNER: gpt-4o
PROGRAMMER: gpt-4o
REVIEWER: gpt-4o       // Change this for better reviews
ROUTER: gpt-4o-mini    // Keep fast
SUMMARIZER: gpt-4o-mini // Keep fast
```

**Result:**
- Slower but highest quality
- Better for complex tasks

---

## 🔍 **Why It Might Feel Slow**

Looking at the logs, typical response times:

1. **Token acquisition:** ~680ms (first request only)
2. **Model initialization:** ~100-200ms
3. **LLM inference:** ~1-4s (depends on complexity)
4. **Tool execution:** Variable (can be 1-30s for shell commands)
5. **Multiple agent calls:** Adds up (Router → Planner → Programmer → Reviewer)

**Total for complex task:** 10-60 seconds is normal

---

## 💡 **Recommended Optimizations (In Order)**

### **1. Enable Streaming (Already Done ✅)**
```typescript
streaming: true
```

### **2. Reduce Max Tokens for Fast Tasks**

Add to `.env`:
```bash
# For faster responses on simple tasks
MAX_TOKENS_ROUTER=1000
MAX_TOKENS_REVIEWER=2000
MAX_TOKENS_PLANNER=4000
MAX_TOKENS_PROGRAMMER=8000
```

Then use these in model initialization.

### **3. Switch to gpt-4o-mini for Planner**

If planning is slow, try:
```typescript
[LLMTask.PLANNER]: {
  modelName: "nvidia-gateway:gpt-4o-mini", // Faster
  temperature: 0,
},
```

### **4. Optimize Prompts**

Shorter, more concise prompts = faster responses.

---

## 📈 **Expected Performance After Optimization**

| Task | Current | Optimized | How |
|------|---------|-----------|-----|
| Router | 2-3s | 1-2s | Already using gpt-4o-mini ✅ |
| Planner | 4-6s | 2-3s | Switch to gpt-4o-mini |
| Programmer | 5-10s | 3-6s | Reduce max_tokens, use streaming |
| Reviewer | 3-5s | 1-2s | Already using gpt-4o-mini ✅ |

**Overall task:** 15-25s → 8-15s (40% faster)

---

## 🧪 **Quick Test: Switch to All gpt-4o-mini**

Want to test if it's faster? Make this change:

**File:** `packages/shared/src/open-swe/llm-task.ts`

```typescript
export const TASK_TO_CONFIG_DEFAULTS_MAP = {
  [LLMTask.PLANNER]: {
    modelName: "nvidia-gateway:gpt-4o-mini", // ← Changed
    temperature: 0,
  },
  [LLMTask.PROGRAMMER]: {
    modelName: "nvidia-gateway:gpt-4o-mini", // ← Changed
    temperature: 0,
  },
  [LLMTask.REVIEWER]: {
    modelName: "nvidia-gateway:gpt-4o-mini", // Already this
    temperature: 0,
  },
  [LLMTask.ROUTER]: {
    modelName: "nvidia-gateway:gpt-4o-mini", // Already this
    temperature: 0,
  },
  [LLMTask.SUMMARIZER]: {
    modelName: "nvidia-gateway:gpt-4o-mini", // Already this
    temperature: 0,
  },
};
```

Save, let hot-reload kick in, and test a task. It should be noticeably faster!

---

## 🎛️ **Advanced: Parallel Tool Execution**

Check if this is enabled:

```typescript
// In tool binding
model.bindTools(tools, {
  parallel_tool_calls: true, // Allow multiple tools to run in parallel
});
```

This can significantly speed up tasks that need multiple tool calls.

---

## 🔍 **Debugging Slow Performance**

**Check these in logs:**

1. **Token acquisition time:**
   ```
   [StarfleetAuth] Token acquired successfully (XXXms)
   ```
   Should be <1s, or <1ms if cached

2. **Model initialization:**
   ```
   [ModelManager] Creating NVIDIA LLM Gateway ChatOpenAI instance
   ```
   Should be instant

3. **LLM inference:**
   ```
   [FallbackRunnable] Model returned successfully
   ```
   Time between "Invoking" and "returned" = LLM time

4. **Tool execution:**
   ```
   [Tool execution logs]
   ```
   Can be slow for shell commands, file operations

---

## 💰 **Cost vs Performance Trade-offs**

| Configuration | Speed | Cost | Quality |
|--------------|-------|------|---------|
| All gpt-4o | Slow | High | Best |
| All gpt-4o-mini | Fast | Low | Good |
| **Mixed (Current)** | **Medium** | **Medium** | **Great** |

**Recommendation:** Stay with current mixed approach for balance.

---

## 🎯 **Action Items**

**To Make It Faster Right Now:**

1. **Switch Planner to gpt-4o-mini:**
   - Edit `packages/shared/src/open-swe/llm-task.ts`
   - Change PLANNER model to `gpt-4o-mini`
   - Save (hot-reload will pick it up)

2. **Switch Programmer to gpt-4o-mini:**
   - Same file
   - Change PROGRAMMER model to `gpt-4o-mini`
   - Save

3. **Test:**
   - Create a new task
   - Should be 40-50% faster

---

## 📝 **Summary**

**Current bottlenecks:**
1. Using gpt-4o for Planner/Programmer (slower but better quality)
2. Large max_tokens (10,000)
3. Multiple sequential agent calls

**Quick fixes:**
1. ✅ Streaming enabled
2. ✅ Token caching active
3. ⏳ Consider switching to gpt-4o-mini for all tasks
4. ⏳ Reduce max_tokens to 4,000-6,000

**Expected improvement:** 40-50% faster with all gpt-4o-mini

---

Would you like me to switch everything to gpt-4o-mini for maximum speed?




