# NVIDIA NIM JSON Tool Call Fix

## Problem

NVIDIA NIM models sometimes return malformed JSON in tool calling responses:

1. **Text before JSON**: LLM generates explanation text before the tool call JSON
   ```
   "I will now analyze the codebase {"command": ["git", "ls-files"]}"
   ```

2. **Tool calls in wrong location**: Response has `tool_calls` in `additional_kwargs` instead of standard location

3. **Over-escaped patterns**: (rare) Double-escaped braces like `{"{"command{"`

## Solution

Created `nvidia-nim-json-fix.ts` utility that:

1. **Extract JSON from text**: Finds first `{` and last `}` to extract JSON
2. **Fix over-escaping**: Removes duplicate brace patterns
3. **Move tool_calls**: Migrates from `additional_kwargs` to standard `tool_calls` location
4. **Robust parsing**: Tries multiple strategies in sequence

## Integration

Applied to two key locations where tool calling happens:

### 1. Manager Router (`classify-message/index.ts`)
```typescript
import { fixNvidiaToolCall } from "../../../../utils/nvidia-nim-json-fix.js";

// After model invocation:
const fixedResponse = fixNvidiaToolCall(response);
const toolCall = fixedResponse.tool_calls?.[0];
```

### 2. Planner (`generate-plan/index.ts`)
```typescript
import { fixNvidiaToolCall } from "../../../../utils/nvidia-nim-json-fix.js";

// After model invocation:
const response = fixNvidiaToolCall(rawResponse);
```

## Testing

### Unit Tests
```bash
node test-nvidia-json-fix.js
```

Tests cover:
- ✅ Valid JSON (passes through unchanged)
- ✅ Text before JSON (extracts successfully)
- ✅ LLM explanation with JSON (extracts successfully)
- ✅ Over-escaped patterns (handled gracefully)

### Integration Tests
```bash
# Test simple tool calling
node test-tool-calling.js

# Test complex tool calling (shell with arrays)
node test-shell-tool-calling.js
```

Both should pass with NVIDIA NIM models:
- `meta/llama-4-scout-17b-16e-instruct`
- `meta/llama-4-maverick-17b-128e-instruct`

## Error Messages

### Before Fix
```
[FallbackRunnable] nvidia-nim:meta/llama-4-scout-17b-16e-instruct failed: 
Expecting ':' delimiter: line 3 column 9 (char 14)
```

### After Fix
```
[NvidiaNimJsonFix] Successfully extracted JSON from text
[ClassifyMessage] Tool call extracted successfully
```

## Fallback Behavior

The fix is **defensive**:
- If JSON is already valid, it passes through unchanged
- If fix strategies fail, error propagates to fallback system
- Fallback system will try next provider (OpenAI → Anthropic)

## Monitoring

Added debug logging to track:
- Which strategy succeeded (direct, extract, fix-escaping, extract-and-fix)
- Tool call structure before/after fix
- Failure cases with JSON preview

## Files Modified

1. **Created**:
   - `apps/open-swe/src/utils/nvidia-nim-json-fix.ts` - Core fix utility

2. **Updated**:
   - `apps/open-swe/src/graphs/manager/nodes/classify-message/index.ts` - Applied fix to router
   - `apps/open-swe/src/graphs/planner/nodes/generate-plan/index.ts` - Applied fix to planner

3. **Test Files**:
   - `test-nvidia-json-fix.js` - Unit tests
   - `test-tool-calling.js` - Integration test (simple schema)
   - `test-shell-tool-calling.js` - Integration test (complex schema)

## Next Steps

If the fix resolves the production issues:
- ✅ Keep using NVIDIA NIM as primary provider (cost savings)
- ✅ Rely on fallback to Anthropic/OpenAI if needed
- ✅ Monitor logs for pattern frequency

If issues persist:
- Consider implementing NVIDIA LLM Gateway fallback (see `NVIDIA_LLM_GATEWAY_SETUP.md`)
- Investigate specific prompts/contexts that trigger the issue
- Report bug to NVIDIA NIM team with examples

## Cost Impact

**NVIDIA NIM vs Anthropic Claude:**
- NVIDIA NIM: ~$0.002 per 1K tokens (80-90% cheaper)
- Claude Sonnet: ~$0.015 per 1K tokens

Even with some fallbacks, overall cost savings should be 60-70%.




