#!/usr/bin/env node

/**
 * Test which models are available through NVIDIA LLM Gateway
 */

console.log('\n🔍 Testing Available Models on NVIDIA LLM Gateway\n');
console.log('=' .repeat(60));

const STARFLEET_ID = "nvssa-prd-rqO3bTP2tJdXh_1hTZKv7-G-mczp6TO8yk-_Vy16spk";
const STARFLEET_SECRET = "ssap-qQ4DO4yVJoo0rdEyU8A";
const STARFLEET_TOKEN_URL = "https://5kbfxgaqc3xgz8nhid1x1r8cfestoypn-trofuum-oc.ssa.nvidia.com/token";
const LLM_GATEWAY_BASE_URL = "https://prod.api.nvidia.com/llm/v1/azure";
const LLM_GATEWAY_API_VERSION = "2024-12-01-preview";

// Models to test
const MODELS_TO_TEST = [
  "gpt-4o",
  "gpt-4o-mini",
  "gpt-4-turbo",
  "gpt-4",
  "gpt-3.5-turbo",
  "gpt-5",
  "gpt-5-mini",
  "gpt-5-nano",
  "o1",
  "o1-mini",
  "o3-mini",
];

// Get Starfleet token
console.log('\n📡 Getting Starfleet token...');
const credentials = Buffer.from(`${STARFLEET_ID}:${STARFLEET_SECRET}`).toString('base64');

const tokenResponse = await fetch(STARFLEET_TOKEN_URL, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/x-www-form-urlencoded',
    'Authorization': `Basic ${credentials}`,
  },
  body: 'grant_type=client_credentials&scope=azureopenai-readwrite',
});

if (!tokenResponse.ok) {
  console.log('❌ Failed to get token');
  process.exit(1);
}

const tokenData = await tokenResponse.json();
const token = tokenData.access_token;
console.log('✅ Token acquired\n');

// Test each model
console.log('Testing models...\n');
console.log('─'.repeat(60));

const results = [];

for (const model of MODELS_TO_TEST) {
  const endpoint = `${LLM_GATEWAY_BASE_URL}/openai/deployments/${model}/chat/completions?api-version=${LLM_GATEWAY_API_VERSION}`;
  
  process.stdout.write(`Testing ${model.padEnd(20)} ... `);
  
  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
        'correlationId': `model-test-${Date.now()}`,
      },
      body: JSON.stringify({
        messages: [
          {
            role: 'user',
            content: 'Say "hi"',
          },
        ],
        max_tokens: 10,
      }),
    });

    if (response.ok) {
      const data = await response.json();
      const actualModel = data.model || model;
      console.log(`✅ AVAILABLE (${actualModel})`);
      results.push({ model, available: true, actualModel, status: response.status });
    } else {
      const error = await response.text();
      console.log(`❌ NOT AVAILABLE (${response.status})`);
      results.push({ model, available: false, error: error.substring(0, 100), status: response.status });
    }
  } catch (error) {
    console.log(`❌ ERROR: ${error.message}`);
    results.push({ model, available: false, error: error.message });
  }
  
  // Small delay to avoid rate limits
  await new Promise(resolve => setTimeout(resolve, 200));
}

// Summary
console.log('\n' + '='.repeat(60));
console.log('\n📊 Summary\n');
console.log('─'.repeat(60));

const available = results.filter(r => r.available);
const unavailable = results.filter(r => !r.available);

console.log(`\n✅ Available Models (${available.length}):`);
available.forEach(r => {
  console.log(`   - ${r.model.padEnd(20)} → ${r.actualModel}`);
});

console.log(`\n❌ Unavailable Models (${unavailable.length}):`);
unavailable.forEach(r => {
  console.log(`   - ${r.model.padEnd(20)} (${r.status || 'error'})`);
});

// Check for GPT-5 specifically
console.log('\n' + '='.repeat(60));
const gpt5Models = available.filter(r => r.model.includes('gpt-5') || r.model.includes('o3'));
if (gpt5Models.length > 0) {
  console.log('\n🎉 GPT-5 family models found:');
  gpt5Models.forEach(r => {
    console.log(`   ✅ ${r.model} (${r.actualModel})`);
  });
} else {
  console.log('\n⚠️  No GPT-5 models available yet');
  console.log('   Available: GPT-4o, GPT-4o-mini, GPT-4-turbo');
}

console.log('\n' + '='.repeat(60) + '\n');




