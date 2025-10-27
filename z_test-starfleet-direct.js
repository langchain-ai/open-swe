#!/usr/bin/env node

/**
 * Direct Starfleet Authentication Test
 * Tests LLM Gateway with hardcoded credentials
 */

console.log('\n🔐 NVIDIA Starfleet Authentication - Direct Test\n');
console.log('=' .repeat(60));

// Hardcoded credentials for testing
const STARFLEET_ID = "nvssa-prd-rqO3bTP2tJdXh_1hTZKv7-G-mczp6TO8yk-_Vy16spk";
const STARFLEET_SECRET = "ssap-qQ4DO4yVJoo0rdEyU8A";
const STARFLEET_TOKEN_URL = "https://5kbfxgaqc3xgz8nhid1x1r8cfestoypn-trofuum-oc.ssa.nvidia.com/token";
const LLM_GATEWAY_BASE_URL = "https://prod.api.nvidia.com/llm/v1/azure";
const LLM_GATEWAY_API_VERSION = "2024-12-01-preview";
const LLM_GATEWAY_MODEL = "gpt-4o-mini";

console.log('\n📋 Configuration:');
console.log('─'.repeat(60));
console.log(`✅ STARFLEET_ID:        ${STARFLEET_ID.substring(0, 20)}...`);
console.log(`✅ STARFLEET_SECRET:    ${STARFLEET_SECRET.substring(0, 10)}...`);
console.log(`✅ TOKEN_URL:           ${STARFLEET_TOKEN_URL}`);
console.log(`✅ GATEWAY_BASE_URL:    ${LLM_GATEWAY_BASE_URL}`);
console.log(`✅ API_VERSION:         ${LLM_GATEWAY_API_VERSION}`);
console.log(`✅ MODEL:               ${LLM_GATEWAY_MODEL}`);

// Test 1: Get Starfleet Token
console.log('\n' + '='.repeat(60));
console.log('\n🧪 Test 1: Acquire Starfleet Access Token\n');
console.log('─'.repeat(60));

async function getStarfleetToken() {
  console.log('📡 Requesting token from Starfleet...');
  console.log(`   URL: ${STARFLEET_TOKEN_URL}`);
  console.log(`   Client ID: ${STARFLEET_ID.substring(0, 20)}...`);

  const credentials = Buffer.from(`${STARFLEET_ID}:${STARFLEET_SECRET}`).toString('base64');
  const startTime = Date.now();
  
  try {
    const response = await fetch(STARFLEET_TOKEN_URL, {
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
    console.log(`   Token: ${data.access_token.substring(0, 50)}...${data.access_token.substring(data.access_token.length - 10)}`);
    console.log(`   Token Length: ${data.access_token.length} characters`);

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

// Test 2: Test LLM Gateway Simple Chat
console.log('\n' + '='.repeat(60));
console.log('\n🧪 Test 2: Test LLM Gateway Simple Chat\n');
console.log('─'.repeat(60));

async function testLLMGateway(accessToken) {
  const endpoint = `${LLM_GATEWAY_BASE_URL}/openai/deployments/${LLM_GATEWAY_MODEL}/chat/completions?api-version=${LLM_GATEWAY_API_VERSION}`;
  const correlationId = `test-${Date.now()}-${Math.random().toString(36).substring(2, 15)}`;

  console.log('📡 Testing LLM Gateway chat completion...');
  console.log(`   Endpoint: ${endpoint}`);
  console.log(`   Model: ${LLM_GATEWAY_MODEL}`);
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
    console.log(`   Response: "${data.choices[0].message.content}"`);
    console.log(`   Tokens: ${data.usage.total_tokens} (prompt: ${data.usage.prompt_tokens}, completion: ${data.usage.completion_tokens})`);

    return true;
  } catch (error) {
    console.log(`\n❌ Error: ${error.message}`);
    return false;
  }
}

const simpleTestSuccess = await testLLMGateway(token);

if (!simpleTestSuccess) {
  console.log('\n❌ Simple chat test failed. Exiting.\n');
  process.exit(1);
}

// Test 3: Test Tool Calling (Critical for Open SWE)
console.log('\n' + '='.repeat(60));
console.log('\n🧪 Test 3: Test Tool Calling (Function Calling)\n');
console.log('─'.repeat(60));

async function testToolCalling(accessToken) {
  const endpoint = `${LLM_GATEWAY_BASE_URL}/openai/deployments/${LLM_GATEWAY_MODEL}/chat/completions?api-version=${LLM_GATEWAY_API_VERSION}`;
  const correlationId = `tool-test-${Date.now()}-${Math.random().toString(36).substring(2, 15)}`;

  console.log('📡 Testing tool calling with complex schema...');
  console.log(`   Model: ${LLM_GATEWAY_MODEL}`);

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
            content: 'I need to route this message to the planner to create a new task.',
          },
        ],
        tools: [
          {
            type: 'function',
            function: {
              name: 'route_message',
              description: 'Route the message to the appropriate handler',
              parameters: {
                type: 'object',
                properties: {
                  internal_reasoning: {
                    type: 'string',
                    description: 'Internal reasoning about the routing decision',
                  },
                  response: {
                    type: 'string',
                    description: 'Response to show the user',
                  },
                  route: {
                    type: 'string',
                    enum: ['start_planner', 'continue_conversation', 'error'],
                    description: 'Where to route the message',
                  },
                },
                required: ['internal_reasoning', 'response', 'route'],
              },
            },
          },
        ],
        tool_choice: 'auto',
        max_tokens: 200,
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
      console.log(`   Arguments (raw): ${toolCall.function.arguments.substring(0, 100)}...`);
      
      // Try to parse the arguments to ensure they're valid JSON
      try {
        const args = JSON.parse(toolCall.function.arguments);
        console.log(`   ✅ Arguments are valid JSON!`);
        console.log(`   Internal Reasoning: "${args.internal_reasoning}"`);
        console.log(`   Response: "${args.response}"`);
        console.log(`   Route: "${args.route}"`);
        
        // Verify all required fields are present
        if (args.internal_reasoning && args.response && args.route) {
          console.log(`   ✅ All required fields present!`);
        } else {
          console.log(`   ❌ Missing required fields!`);
          return false;
        }
      } catch (e) {
        console.log(`   ❌ Arguments are NOT valid JSON: ${e.message}`);
        console.log(`   Raw arguments: ${toolCall.function.arguments}`);
        return false;
      }
    } else {
      console.log(`\n⚠️  No tool calls in response (${elapsed}ms)`);
      console.log(`   Content: ${message.content}`);
      return false;
    }

    return true;
  } catch (error) {
    console.log(`\n❌ Error: ${error.message}`);
    return false;
  }
}

const toolCallingSuccess = await testToolCalling(token);

// Test 4: Test Multiple Concurrent Requests (Token Reuse)
console.log('\n' + '='.repeat(60));
console.log('\n🧪 Test 4: Test Concurrent Requests (Token Reuse)\n');
console.log('─'.repeat(60));

async function testConcurrentRequests(accessToken) {
  console.log('📡 Sending 3 concurrent requests...');
  
  const promises = [
    testLLMGateway(accessToken),
    testLLMGateway(accessToken),
    testLLMGateway(accessToken),
  ];
  
  const startTime = Date.now();
  const results = await Promise.all(promises);
  const elapsed = Date.now() - startTime;
  
  const allSuccess = results.every(r => r === true);
  
  if (allSuccess) {
    console.log(`\n✅ All 3 concurrent requests succeeded! (${elapsed}ms total)`);
  } else {
    console.log(`\n❌ Some concurrent requests failed`);
  }
  
  return allSuccess;
}

const concurrentSuccess = await testConcurrentRequests(token);

// Summary
console.log('\n' + '='.repeat(60));
console.log('\n📊 Test Summary\n');
console.log('─'.repeat(60));
console.log(`${token ? '✅' : '❌'} Starfleet Token:       ${token ? 'SUCCESS' : 'FAILED'}`);
console.log(`${simpleTestSuccess ? '✅' : '❌'} Simple Chat:           ${simpleTestSuccess ? 'SUCCESS' : 'FAILED'}`);
console.log(`${toolCallingSuccess ? '✅' : '❌'} Tool Calling:          ${toolCallingSuccess ? 'SUCCESS' : 'FAILED'}`);
console.log(`${concurrentSuccess ? '✅' : '❌'} Concurrent Requests:   ${concurrentSuccess ? 'SUCCESS' : 'FAILED'}`);

console.log('\n' + '='.repeat(60));

if (token && simpleTestSuccess && toolCallingSuccess && concurrentSuccess) {
  console.log('\n🎉 All tests passed! NVIDIA LLM Gateway is fully operational!\n');
  console.log('✅ Starfleet authentication works');
  console.log('✅ LLM Gateway responds correctly');
  console.log('✅ Tool calling returns valid JSON (no corruption!)');
  console.log('✅ Token can be reused across requests');
  console.log('\nNext steps:');
  console.log('1. Add these credentials to apps/open-swe/.env');
  console.log('2. Set NVIDIA_LLM_GATEWAY_ENABLED=true');
  console.log('3. Restart Open SWE server: yarn dev');
  console.log('4. LLM Gateway will be used as fallback when NIM fails\n');
  process.exit(0);
} else {
  console.log('\n❌ Some tests failed. Check the output above.\n');
  process.exit(1);
}




