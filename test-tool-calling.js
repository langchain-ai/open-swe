#!/usr/bin/env node
/**
 * NVIDIA NIM Tool Calling Test Script
 * Tests tool calling with multiple NVIDIA NIM models
 * Run: node test-tool-calling.js
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
  'meta/llama-4-scout-17b-16e-instruct',       // Llama 4 Scout (testing now)
  'meta/llama-4-maverick-17b-128e-instruct',   // Llama 4 Maverick (known to work)
];

console.log('\n========================================');
console.log('  NVIDIA NIM Tool Calling Test');
console.log('========================================\n');

// Define the tool schema (same as Open SWE)
const classificationSchema = z.object({
  internal_reasoning: z.string().describe("Your reasoning for the route choice"),
  response: z.string().describe("Response to send to the user"),
  route: z.enum(["start_planner", "no_op"]).describe("The route to take"),
});

const respondAndRouteTool = {
  name: "respond_and_route",
  description: "Respond to the user's message and determine how to route it.",
  schema: classificationSchema,
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

    const modelWithTools = model.bindTools([respondAndRouteTool], {
      tool_choice: respondAndRouteTool.name,
    });

    console.log('Invoking model with tool calling...\n');

    const response = await modelWithTools.invoke([
      {
        role: "system",
        content: "You are a helpful assistant. Always use the respond_and_route tool to respond.",
      },
      {
        role: "user",
        content: "Please help me fix a bug",
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
    } else if (hasAdditionalToolCalls) {
      console.log('⚠️  Tool calls in additional_kwargs');
      const toolCall = response.additional_kwargs.tool_calls[0];
      console.log('Arguments preview:', toolCall.function?.arguments?.substring(0, 100));
      
      // Try to parse
      try {
        const parsed = JSON.parse(toolCall.function.arguments);
        console.log('✅ Successfully parsed!');
        console.log(JSON.stringify(parsed, null, 2));
      } catch (e) {
        console.log('❌ JSON parsing failed:', e.message);
        console.log('First 200 chars:', toolCall.function.arguments.substring(0, 200));
      }
    } else {
      console.log('❌ No tool calls found');
    }

  } catch (error) {
    console.log(`❌ ${MODEL} failed:`);
    console.log('Error:', error.message);
  }
}

console.log('\n' + '='.repeat(60));
console.log('  TEST COMPLETE');
console.log('='.repeat(60) + '\n');
