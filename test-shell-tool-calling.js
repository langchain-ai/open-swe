#!/usr/bin/env node
/**
 * NVIDIA NIM Shell Tool Calling Test Script
 * Tests the complex shell tool schema that's actually failing
 * Run: node test-shell-tool-calling.js
 */

import dotenv from 'dotenv';
import { ChatOpenAI } from '@langchain/openai';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { z } from 'zod';

// Load .env file
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
dotenv.config({ path: join(__dirname, 'apps', 'open-swe', '.env') });

const NVIDIA_NIM_API_KEY = process.env.NVIDIA_NIM_API_KEY;
const NVIDIA_NIM_BASE_URL = process.env.NVIDIA_NIM_BASE_URL || 'https://integrate.api.nvidia.com/v1';

// Test multiple models
const MODELS_TO_TEST = [
  'meta/llama-4-scout-17b-16e-instruct',       // Llama 4 Scout
  'meta/llama-4-maverick-17b-128e-instruct',   // Llama 4 Maverick
];

console.log('\n========================================');
console.log('  NVIDIA NIM Shell Tool Test');
console.log('========================================\n');

// Define the ACTUAL shell tool schema from Open SWE
const shellToolSchema = z.object({
  command: z
    .array(z.string())
    .describe(
      "The command to run. Ensure the command is properly formatted, with arguments in the correct order, and including any wrapping strings, quotes, etc. By default, this command will be executed in the root of the repository, unless a custom workdir is specified.",
    ),
  workdir: z
    .string()
    .optional()
    .describe(
      `The working directory for the command. Defaults to the root of the repository. You should only specify this if the command you're running can not be executed from the root of the repository.`,
    ),
  timeout: z
    .number()
    .optional()
    .describe(
      "The maximum time to wait for the command to complete in seconds. For commands which may require a long time to complete, such as running tests, you should increase this value.",
    ),
});

const shellTool = {
  name: "shell",
  description: "Runs a shell command, and returns its output.",
  schema: shellToolSchema,
};

// Test each model
for (const MODEL of MODELS_TO_TEST) {
  console.log('\n' + '='.repeat(60));
  console.log(`  TESTING: ${MODEL}`);
  console.log('='.repeat(60) + '\n');

  try {
    const model = new ChatOpenAI({
      modelName: MODEL,
      openAIApiKey: NVIDIA_NIM_API_KEY,
      configuration: {
        baseURL: NVIDIA_NIM_BASE_URL,
      },
      maxRetries: 3,
      maxTokens: 1000,
      temperature: 0,
      streaming: false,
    });

    const modelWithTools = model.bindTools([shellTool], {
      tool_choice: shellTool.name,
    });

    console.log('Invoking model with shell tool calling...\n');

    const response = await modelWithTools.invoke([
      {
        role: "system",
        content: "You are a helpful coding assistant. Use the shell tool to run commands. Always use the shell tool to respond.",
      },
      {
        role: "user",
        content: "List all files in the repository using git ls-files",
      },
    ]);

    console.log('✅ Model responded!\n');
    
    // Check tool calls
    const hasStandardToolCalls = response.tool_calls && response.tool_calls.length > 0;
    const hasAdditionalToolCalls = response.additional_kwargs?.tool_calls && response.additional_kwargs.tool_calls.length > 0;
    
    console.log('Standard tool_calls:', response.tool_calls?.length || 0);
    console.log('Additional tool_calls:', response.additional_kwargs?.tool_calls?.length || 0);
    console.log('');

    if (hasStandardToolCalls) {
      console.log('✅ SUCCESS! Tool calls in standard location');
      const toolCall = response.tool_calls[0];
      console.log('Tool:', toolCall.name);
      console.log('Args:', JSON.stringify(toolCall.args, null, 2));
      
      // Validate the structure
      if (Array.isArray(toolCall.args.command)) {
        console.log('✅ Command is an array (correct!)');
        console.log('   Command:', toolCall.args.command.join(' '));
      } else {
        console.log('❌ Command is NOT an array:', typeof toolCall.args.command);
      }
    } else if (hasAdditionalToolCalls) {
      console.log('⚠️  Tool calls in additional_kwargs');
      const toolCall = response.additional_kwargs.tool_calls[0];
      console.log('Raw function.arguments:', toolCall.function?.arguments?.substring(0, 300));
      console.log('');
      
      // Try to parse
      try {
        const parsed = JSON.parse(toolCall.function.arguments);
        console.log('✅ Successfully parsed!');
        console.log('Parsed args:', JSON.stringify(parsed, null, 2));
        
        // Validate the structure
        if (Array.isArray(parsed.command)) {
          console.log('✅ Command is an array (correct!)');
          console.log('   Command:', parsed.command.join(' '));
        } else {
          console.log('❌ Command is NOT an array:', typeof parsed.command);
        }
      } catch (e) {
        console.log('❌ JSON parsing failed:', e.message);
        console.log('\nFirst 500 chars of corrupted JSON:');
        console.log(toolCall.function.arguments.substring(0, 500));
        console.log('\n...attempting to show pattern...');
        
        // Try to identify the corruption pattern
        const corruptedJson = toolCall.function.arguments;
        const firstBrace = corruptedJson.substring(0, 50);
        console.log('Pattern at start:', JSON.stringify(firstBrace));
        
        // Try some cleanup strategies
        console.log('\n🔧 Attempting cleanup strategies:');
        
        // Strategy 1: Remove duplicate braces/quotes
        try {
          let cleaned = corruptedJson.replace(/\{"\{"/g, '{"');
          cleaned = cleaned.replace(/\}"\}"/g, '"}');
          const parsed = JSON.parse(cleaned);
          console.log('  ✅ Strategy 1 (remove duplicates) worked!');
          console.log('  Result:', JSON.stringify(parsed, null, 2));
        } catch (e2) {
          console.log('  ❌ Strategy 1 failed:', e2.message);
        }
        
        // Strategy 2: Try to extract first valid JSON object
        try {
          const match = corruptedJson.match(/\{[^{}]*"command"[^{}]*\}/);
          if (match) {
            const parsed = JSON.parse(match[0]);
            console.log('  ✅ Strategy 2 (extract pattern) worked!');
            console.log('  Result:', JSON.stringify(parsed, null, 2));
          } else {
            console.log('  ❌ Strategy 2: No matching pattern found');
          }
        } catch (e2) {
          console.log('  ❌ Strategy 2 failed:', e2.message);
        }
      }
    } else {
      console.log('❌ No tool calls found');
      console.log('Response content:', response.content);
    }

  } catch (error) {
    console.log(`❌ ${MODEL} failed:`)
;
    console.log('Error:', error.message);
    if (error.stack) {
      console.log('Stack:', error.stack.substring(0, 500));
    }
  }
}

console.log('\n' + '='.repeat(60));
console.log('  TEST COMPLETE');
console.log('='.repeat(60) + '\n');




