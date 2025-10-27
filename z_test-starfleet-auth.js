#!/usr/bin/env node

/**
 * Test script for NVIDIA Starfleet authentication
 * 
 * This script tests:
 * 1. Starfleet token acquisition
 * 2. Token caching and expiry
 * 3. NVIDIA LLM Gateway API connectivity
 * 
 * Usage:
 *   cd C:\Users\idant\Code\open-swe\open-swe
 *   node z_test-starfleet-auth.js
 */

import dotenv from 'dotenv';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

// Load environment variables from apps/open-swe/.env
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
dotenv.config({ path: join(__dirname, 'apps', 'open-swe', '.env') });

console.log('\n🔐 NVIDIA Starfleet Authentication Test\n');
console.log('=' .repeat(60));

// Check environment variables
console.log('\n📋 Environment Configuration:');
console.log('─'.repeat(60));

const requiredVars = [
  'STARFLEET_ID',
  'STARFLEET_SECRET',
  'STARFLEET_TOKEN_URL',
  'LLM_GATEWAY_BASE_URL',
  'LLM_GATEWAY_API_VERSION',
  'LLM_GATEWAY_MODEL',
];

let allConfigured = true;
for (const varName of requiredVars) {
  const value = process.env[varName];
  const hasValue = !!value;
  const display = hasValue 
    ? (varName.includes('SECRET') ? '***' + value.slice(-4) : value.substring(0, 30) + (value.length > 30 ? '...' : ''))
    : '❌ NOT SET';
  
  console.log(`${hasValue ? '✅' : '❌'} ${varName.padEnd(30)} = ${display}`);
  
  if (!hasValue) {
    allConfigured = false;
  }
}

if (!allConfigured) {
  console.log('\n❌ Missing required environment variables!');
  console.log('\nPlease add these to apps/open-swe/.env:');
  console.log(`
STARFLEET_ID=nvssa-prd...
STARFLEET_SECRET=ssap-...
STARFLEET_TOKEN_URL=https://5kbfxgaqc3xgz8nhid1x1r8cfestoypn-trofuum-oc.ssa.nvidia.com/token
LLM_GATEWAY_BASE_URL=https://prod.api.nvidia.com/llm/v1/azure
LLM_GATEWAY_API_VERSION=2024-12-01-preview
LLM_GATEWAY_MODEL=gpt-4o-mini
  `);
  process.exit(1);
}

console.log('\n✅ All environment variables configured!\n');

// Test 1: Get Starfleet Token
console.log('=' .repeat(60));
console.log('\n🧪 Test 1: Acquire Starfleet Access Token\n');
console.log('─'.repeat(60));

async function getStarfleetToken() {
  const tokenUrl = process.env.STARFLEET_TOKEN_URL;
  const clientId = process.env.STARFLEET_ID;
  const clientSecret = process.env.STARFLEET_SECRET;

  console.log('📡 Requesting token from Starfleet...');
  console.log(`   URL: ${tokenUrl}`);
  console.log(`   Client ID: ${clientId.substring(0, 15)}...`);

  const credentials = Buffer.from(`${clientId}:${clientSecret}`).toString('base64');

  const startTime = Date.now();
  
  try {
    const response = await fetch(tokenUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': `Basic ${credentials}`,
      },
      body: 'grant_type=client_credentials&scope=azureopenai-readwrite',
    });

    const elapsed = Date.now() - startTime;

    if (!response.ok) {
      const errorText = await response.text();
      console.log(`\n❌ Token request failed! (${elapsed}ms)`);
      console.log(`   Status: ${response.status} ${response.statusText}`);
      console.log(`   Error: ${errorText}`);
      return null;
    }

    const data = await response.json();
    console.log(`\n✅ Token acquired successfully! (${elapsed}ms)`);
    console.log(`   Token Type: ${data.token_type}`);
    console.log(`   Expires In: ${data.expires_in}s (${Math.floor(data.expires_in / 60)} minutes)`);
    console.log(`   Token: ${data.access_token.substring(0, 50)}...`);

    return data.access_token;
  } catch (error) {
    console.log(`\n❌ Error: ${error.message}`);
    return null;
  }
}

const token = await getStarfleetToken();

if (!token) {
  console.log('\n❌ Failed to get Starfleet token. Exiting.\n');
  process.exit(1);
}

// Test 2: Test LLM Gateway API
console.log('\n' + '='.repeat(60));
console.log('\n🧪 Test 2: Test NVIDIA LLM Gateway API\n');
console.log('─'.repeat(60));

async function testLLMGateway(accessToken) {
  const baseURL = process.env.LLM_GATEWAY_BASE_URL;
  const apiVersion = process.env.LLM_GATEWAY_API_VERSION;
  const model = process.env.LLM_GATEWAY_MODEL || 'gpt-4o-mini';
  
  const endpoint = `${baseURL}/openai/deployments/${model}/chat/completions?api-version=${apiVersion}`;
  const correlationId = `test-${Date.now()}-${Math.random().toString(36).substring(2, 15)}`;

  console.log('📡 Testing LLM Gateway chat completion...');
  console.log(`   Endpoint: ${endpoint}`);
  console.log(`   Model: ${model}`);
  console.log(`   Correlation ID: ${correlationId}`);

  const startTime = Date.now();

  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${accessToken}`,
        'correlationId': correlationId,
      },
      body: JSON.stringify({
        messages: [
          {
            role: 'user',
            content: 'Say "Hello from NVIDIA LLM Gateway!" and nothing else.',
          },
        ],
        max_tokens: 50,
        temperature: 0.7,
      }),
    });

    const elapsed = Date.now() - startTime;

    if (!response.ok) {
      const errorText = await response.text();
      console.log(`\n❌ LLM Gateway request failed! (${elapsed}ms)`);
      console.log(`   Status: ${response.status} ${response.statusText}`);
      console.log(`   Error: ${errorText}`);
      return false;
    }

    const data = await response.json();
    console.log(`\n✅ LLM Gateway response received! (${elapsed}ms)`);
    console.log(`   Model: ${data.model}`);
    console.log(`   Response: ${data.choices[0].message.content}`);
    console.log(`   Tokens Used: ${data.usage.total_tokens} (prompt: ${data.usage.prompt_tokens}, completion: ${data.usage.completion_tokens})`);

    return true;
  } catch (error) {
    console.log(`\n❌ Error: ${error.message}`);
    return false;
  }
}

const gatewaySuccess = await testLLMGateway(token);

// Test 3: Test Tool Calling
console.log('\n' + '='.repeat(60));
console.log('\n🧪 Test 3: Test Tool Calling (Function Calling)\n');
console.log('─'.repeat(60));

async function testToolCalling(accessToken) {
  const baseURL = process.env.LLM_GATEWAY_BASE_URL;
  const apiVersion = process.env.LLM_GATEWAY_API_VERSION;
  const model = process.env.LLM_GATEWAY_MODEL || 'gpt-4o-mini';
  
  const endpoint = `${baseURL}/openai/deployments/${model}/chat/completions?api-version=${apiVersion}`;
  const correlationId = `tool-test-${Date.now()}-${Math.random().toString(36).substring(2, 15)}`;

  console.log('📡 Testing tool calling...');
  console.log(`   Endpoint: ${endpoint}`);
  console.log(`   Model: ${model}`);

  const startTime = Date.now();

  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${accessToken}`,
        'correlationId': correlationId,
      },
      body: JSON.stringify({
        messages: [
          {
            role: 'user',
            content: 'What is the weather in San Francisco?',
          },
        ],
        tools: [
          {
            type: 'function',
            function: {
              name: 'get_weather',
              description: 'Get the current weather in a location',
              parameters: {
                type: 'object',
                properties: {
                  location: {
                    type: 'string',
                    description: 'The city name',
                  },
                  unit: {
                    type: 'string',
                    enum: ['celsius', 'fahrenheit'],
                  },
                },
                required: ['location'],
              },
            },
          },
        ],
        tool_choice: 'auto',
        max_tokens: 100,
      }),
    });

    const elapsed = Date.now() - startTime;

    if (!response.ok) {
      const errorText = await response.text();
      console.log(`\n❌ Tool calling request failed! (${elapsed}ms)`);
      console.log(`   Status: ${response.status} ${response.statusText}`);
      console.log(`   Error: ${errorText}`);
      return false;
    }

    const data = await response.json();
    const message = data.choices[0].message;

    if (message.tool_calls && message.tool_calls.length > 0) {
      const toolCall = message.tool_calls[0];
      console.log(`\n✅ Tool calling works! (${elapsed}ms)`);
      console.log(`   Function: ${toolCall.function.name}`);
      console.log(`   Arguments: ${toolCall.function.arguments}`);
      
      // Try to parse the arguments to ensure they're valid JSON
      try {
        const args = JSON.parse(toolCall.function.arguments);
        console.log(`   ✅ Arguments are valid JSON`);
        console.log(`   Location: ${args.location}`);
        if (args.unit) console.log(`   Unit: ${args.unit}`);
      } catch (e) {
        console.log(`   ❌ Arguments are NOT valid JSON: ${e.message}`);
        return false;
      }
    } else {
      console.log(`\n⚠️  No tool calls in response (${elapsed}ms)`);
      console.log(`   Content: ${message.content}`);
    }

    return true;
  } catch (error) {
    console.log(`\n❌ Error: ${error.message}`);
    return false;
  }
}

const toolCallingSuccess = await testToolCalling(token);

// Summary
console.log('\n' + '='.repeat(60));
console.log('\n📊 Test Summary\n');
console.log('─'.repeat(60));
console.log(`✅ Starfleet Token:     ${token ? 'SUCCESS' : 'FAILED'}`);
console.log(`${gatewaySuccess ? '✅' : '❌'} LLM Gateway API:    ${gatewaySuccess ? 'SUCCESS' : 'FAILED'}`);
console.log(`${toolCallingSuccess ? '✅' : '❌'} Tool Calling:        ${toolCallingSuccess ? 'SUCCESS' : 'FAILED'}`);

console.log('\n' + '='.repeat(60));

if (token && gatewaySuccess && toolCallingSuccess) {
  console.log('\n🎉 All tests passed! NVIDIA LLM Gateway is ready to use.\n');
  console.log('Next steps:');
  console.log('1. Make sure apps/open-swe/.env has all the Starfleet credentials');
  console.log('2. Set NVIDIA_LLM_GATEWAY_ENABLED=true in .env');
  console.log('3. Restart the Open SWE server: yarn dev');
  console.log('4. The fallback order will be: nvidia-nim → nvidia-gateway → others\n');
  process.exit(0);
} else {
  console.log('\n❌ Some tests failed. Please check your configuration.\n');
  process.exit(1);
}




