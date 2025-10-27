#!/usr/bin/env node
/**
 * NVIDIA NIM API Test Script
 * Tests if your NVIDIA NIM API key and configuration are working
 * Run: node test-nvidia-nim.js
 */

import dotenv from 'dotenv';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

// Load .env file
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
dotenv.config({ path: join(__dirname, 'apps', 'open-swe', '.env') });

const NVIDIA_NIM_API_KEY = process.env.NVIDIA_NIM_API_KEY;
const NVIDIA_NIM_BASE_URL = process.env.NVIDIA_NIM_BASE_URL || 'https://integrate.api.nvidia.com/v1';
const PRIMARY_MODEL = process.env.NVIDIA_NIM_PRIMARY_MODEL || 'meta/llama-3.3-70b-instruct';
const FALLBACK_MODEL = process.env.NVIDIA_NIM_FALLBACK_MODEL || 'qwen/qwen3-235b-a22b';

console.log('\n========================================');
console.log('  NVIDIA NIM Configuration Test');
console.log('========================================\n');

// Step 1: Check configuration
console.log('[STEP 1] Checking configuration...\n');

console.log('API Key:', NVIDIA_NIM_API_KEY ? `${NVIDIA_NIM_API_KEY.substring(0, 15)}...` : '❌ NOT FOUND');
console.log('Base URL:', NVIDIA_NIM_BASE_URL);
console.log('Primary Model:', PRIMARY_MODEL);
console.log('Fallback Model:', FALLBACK_MODEL);

if (!NVIDIA_NIM_API_KEY) {
  console.log('\n❌ ERROR: NVIDIA_NIM_API_KEY not found in .env file!');
  console.log('   Location: apps/open-swe/.env');
  console.log('   Add: NVIDIA_NIM_API_KEY=nvapi-YOUR_KEY_HERE\n');
  process.exit(1);
}

if (NVIDIA_NIM_API_KEY.startsWith('nvapi-nvapi-')) {
  console.log('\n⚠️  WARNING: API key has double "nvapi-" prefix!');
  console.log('   Current:', NVIDIA_NIM_API_KEY.substring(0, 20) + '...');
  console.log('   Should be:', NVIDIA_NIM_API_KEY.replace('nvapi-nvapi-', 'nvapi-').substring(0, 20) + '...\n');
}

console.log('\n✅ Configuration loaded\n');

// Step 2: Test API connectivity
console.log('[STEP 2] Testing NVIDIA NIM API...\n');

async function testNvidiaAPI(modelName) {
  const url = `${NVIDIA_NIM_BASE_URL}/chat/completions`;
  
  console.log(`Testing model: ${modelName}`);
  console.log(`Endpoint: ${url}`);
  console.log(`Making request...\n`);

  const payload = {
    model: modelName,
    messages: [
      {
        role: 'user',
        content: 'Say "Hello from NVIDIA NIM!" in exactly those words.'
      }
    ],
    temperature: 0.7,
    max_tokens: 50
  };

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${NVIDIA_NIM_API_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload)
    });

    const data = await response.json();

    if (!response.ok) {
      console.log('❌ API Error:');
      console.log('   Status:', response.status, response.statusText);
      console.log('   Error:', JSON.stringify(data, null, 2));
      return false;
    }

    console.log('✅ SUCCESS! API is working!');
    console.log('\nResponse:');
    console.log('   Model:', data.model);
    console.log('   Message:', data.choices[0].message.content);
    console.log('   Tokens Used:', data.usage.total_tokens);
    console.log('   Prompt Tokens:', data.usage.prompt_tokens);
    console.log('   Completion Tokens:', data.usage.completion_tokens);
    
    return true;
  } catch (error) {
    console.log('❌ Request Failed:');
    console.log('   Error:', error.message);
    if (error.cause) {
      console.log('   Cause:', error.cause);
    }
    return false;
  }
}

// Test primary model
console.log('─'.repeat(50));
console.log('Testing PRIMARY model');
console.log('─'.repeat(50) + '\n');

const primarySuccess = await testNvidiaAPI(PRIMARY_MODEL);

console.log('\n' + '─'.repeat(50));
console.log('Testing FALLBACK model');
console.log('─'.repeat(50) + '\n');

const fallbackSuccess = await testNvidiaAPI(FALLBACK_MODEL);

// Summary
console.log('\n' + '='.repeat(50));
console.log('  TEST SUMMARY');
console.log('='.repeat(50) + '\n');

console.log('Primary Model (' + PRIMARY_MODEL + '):');
console.log('  ', primarySuccess ? '✅ WORKING' : '❌ FAILED');

console.log('\nFallback Model (' + FALLBACK_MODEL + '):');
console.log('  ', fallbackSuccess ? '✅ WORKING' : '❌ FAILED');

if (primarySuccess && fallbackSuccess) {
  console.log('\n🎉 All tests passed! NVIDIA NIM is configured correctly!');
  console.log('   You can now use NVIDIA NIM in Open SWE\n');
  process.exit(0);
} else if (primarySuccess || fallbackSuccess) {
  console.log('\n⚠️  Partial success. At least one model is working.');
  console.log('   Open SWE will work but may fall back more often\n');
  process.exit(0);
} else {
  console.log('\n❌ Both models failed. Please check:');
  console.log('   1. API key is correct (no typos)');
  console.log('   2. API key is active at https://build.nvidia.com/');
  console.log('   3. You have credits available');
  console.log('   4. Network connection is working\n');
  process.exit(1);
}

