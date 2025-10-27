/**
 * NVIDIA NIM JSON Tool Call Fixer
 * 
 * NVIDIA NIM sometimes returns malformed JSON in tool calls:
 * 1. Text before JSON: "I will now... {"command": [...]}
 * 2. Over-escaped braces: {"{"command{"{...
 * 3. Mixed format: tool_calls in additional_kwargs instead of standard location
 * 
 * This utility provides cleanup functions to fix these issues.
 */

import { createLogger, LogLevel } from "./logger.js";

const logger = createLogger(LogLevel.DEBUG, "NvidiaNimJsonFix");

export interface ToolCallFix {
  success: boolean;
  parsed?: any;
  error?: string;
  strategy?: string;
}

/**
 * Extract JSON from text that may have content before/after the JSON
 * Example: "I will proceed with {"command": ["git", "ls-files"]}"
 */
function extractJsonFromText(text: string): string | null {
  // Strategy 1: Find first { and last }
  const firstBrace = text.indexOf('{');
  const lastBrace = text.lastIndexOf('}');
  
  if (firstBrace !== -1 && lastBrace !== -1 && lastBrace > firstBrace) {
    return text.substring(firstBrace, lastBrace + 1);
  }
  
  return null;
}

/**
 * Fix over-escaped JSON patterns common in NVIDIA NIM responses
 * Example: {"{"command{"{"... → {"command...
 */
function fixOverEscapedJson(jsonString: string): string {
  let fixed = jsonString;
  
  // Pattern 1: Remove duplicate opening braces with quotes
  fixed = fixed.replace(/\{"\{"/g, '{"');
  
  // Pattern 2: Remove duplicate closing braces with quotes
  fixed = fixed.replace(/\}"\}"/g, '"}');
  
  // Pattern 3: Fix escaped quotes that shouldn't be escaped
  fixed = fixed.replace(/\\\\"/g, '\\"');
  
  // Pattern 4: Remove repeating { patterns
  fixed = fixed.replace(/(\{"\{)+/g, '{"');
  
  return fixed;
}

/**
 * Try multiple strategies to parse potentially malformed JSON from NVIDIA NIM
 */
export function parseNvidiaToolCallJson(rawJsonString: string): ToolCallFix {
  if (!rawJsonString || typeof rawJsonString !== 'string') {
    return {
      success: false,
      error: 'Input is not a string or is empty',
    };
  }

  // Strategy 1: Direct parse (optimistic - works if JSON is valid)
  try {
    const parsed = JSON.parse(rawJsonString);
    return {
      success: true,
      parsed,
      strategy: 'direct',
    };
  } catch (directError) {
    logger.debug('Direct JSON parse failed', {
      error: directError instanceof Error ? directError.message : String(directError),
      preview: rawJsonString.substring(0, 100),
    });
  }

  // Strategy 2: Extract JSON from surrounding text
  const extracted = extractJsonFromText(rawJsonString);
  if (extracted) {
    try {
      const parsed = JSON.parse(extracted);
      logger.info('Successfully extracted JSON from text', {
        originalLength: rawJsonString.length,
        extractedLength: extracted.length,
      });
      return {
        success: true,
        parsed,
        strategy: 'extract',
      };
    } catch (extractError) {
      logger.debug('Extracted JSON parse failed', {
        error: extractError instanceof Error ? extractError.message : String(extractError),
      });
    }
  }

  // Strategy 3: Fix over-escaped JSON
  const fixed = fixOverEscapedJson(rawJsonString);
  if (fixed !== rawJsonString) {
    try {
      const parsed = JSON.parse(fixed);
      logger.info('Successfully fixed over-escaped JSON', {
        originalLength: rawJsonString.length,
        fixedLength: fixed.length,
      });
      return {
        success: true,
        parsed,
        strategy: 'fix-escaping',
      };
    } catch (fixError) {
      logger.debug('Fixed JSON parse failed', {
        error: fixError instanceof Error ? fixError.message : String(fixError),
      });
    }
  }

  // Strategy 4: Combine extraction + fixing
  if (extracted && extracted !== rawJsonString) {
    const fixedExtracted = fixOverEscapedJson(extracted);
    try {
      const parsed = JSON.parse(fixedExtracted);
      logger.info('Successfully parsed after extract + fix', {
        strategy: 'extract-and-fix',
      });
      return {
        success: true,
        parsed,
        strategy: 'extract-and-fix',
      };
    } catch (combinedError) {
      logger.debug('Extract + fix parse failed', {
        error: combinedError instanceof Error ? combinedError.message : String(combinedError),
      });
    }
  }

  // All strategies failed
  return {
    success: false,
    error: 'All parsing strategies failed',
  };
}

/**
 * Fix NVIDIA NIM tool call that might be in additional_kwargs or malformed
 */
export function fixNvidiaToolCall(response: any): any {
  logger.debug('fixNvidiaToolCall called', {
    hasToolCalls: !!response.tool_calls,
    toolCallsLength: response.tool_calls?.length,
    hasAdditionalKwargs: !!response.additional_kwargs,
    hasAdditionalToolCalls: !!response.additional_kwargs?.tool_calls,
    additionalToolCallsLength: response.additional_kwargs?.tool_calls?.length,
    responseType: typeof response,
    responseKeys: Object.keys(response || {}),
  });

  // Check if tool_calls are already in the standard location and valid
  if (response.tool_calls && response.tool_calls.length > 0) {
    const firstToolCall = response.tool_calls[0];
    if (firstToolCall.args && typeof firstToolCall.args === 'object') {
      // Standard location with parsed args - already good
      logger.debug('Tool calls already in standard location and parsed', {
        toolName: firstToolCall.name,
        argsType: typeof firstToolCall.args,
      });
      return response;
    }
  }

  // Check additional_kwargs for NVIDIA NIM responses
  const additionalToolCalls = response.additional_kwargs?.tool_calls;
  if (!additionalToolCalls || additionalToolCalls.length === 0) {
    // No tool calls to fix
    logger.debug('No additional tool calls found to fix');
    return response;
  }

  logger.info('Attempting to fix NVIDIA NIM tool call from additional_kwargs', {
    toolCallsCount: additionalToolCalls.length,
  });

  const fixedToolCalls = [];

  for (const rawToolCall of additionalToolCalls) {
    const argsString = rawToolCall.function?.arguments;
    
    logger.debug('Processing tool call from additional_kwargs', {
      toolName: rawToolCall.function?.name || rawToolCall.name,
      hasArguments: !!argsString,
      argumentsType: typeof argsString,
      argumentsLength: argsString?.length,
      argumentsPreview: typeof argsString === 'string' ? argsString.substring(0, 100) : 'N/A',
    });
    
    if (!argsString) {
      logger.warn('Tool call missing function.arguments', { rawToolCall });
      continue;
    }

    if (typeof argsString !== 'string') {
      // Arguments already parsed
      logger.debug('Arguments already parsed (not a string)', {
        argsType: typeof argsString,
      });
      fixedToolCalls.push({
        name: rawToolCall.function?.name || rawToolCall.name,
        args: argsString,
        id: rawToolCall.id,
      });
      continue;
    }

    // Try to parse/fix the JSON
    logger.debug('Attempting to parse JSON arguments', {
      argsLength: argsString.length,
      firstChars: argsString.substring(0, 50),
      lastChars: argsString.length > 50 ? argsString.substring(argsString.length - 50) : '',
    });
    
    const parseResult = parseNvidiaToolCallJson(argsString);
    
    if (parseResult.success) {
      logger.info('Successfully fixed tool call', {
        toolName: rawToolCall.function?.name || rawToolCall.name,
        strategy: parseResult.strategy,
      });
      
      fixedToolCalls.push({
        name: rawToolCall.function?.name || rawToolCall.name,
        args: parseResult.parsed,
        id: rawToolCall.id,
      });
    } else {
      logger.error('❌ FAILED TO PARSE tool call arguments - ALL STRATEGIES FAILED', {
        toolName: rawToolCall.function?.name || rawToolCall.name,
        error: parseResult.error,
        argsLength: argsString.length,
        fullArgs: argsString, // Log the FULL corrupted JSON
        argsPreview: argsString.substring(0, 300),
        charCodes: argsString.substring(0, 50).split('').map((c: string) => c.charCodeAt(0)),
      });
    }
  }

  // Return modified response with fixed tool_calls in standard location
  if (fixedToolCalls.length > 0) {
    return {
      ...response,
      tool_calls: fixedToolCalls,
    };
  }

  return response;
}

