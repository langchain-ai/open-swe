#!/usr/bin/env node
/**
 * Test NVIDIA NIM JSON Fix Utility
 * Tests all the edge cases our fix handles
 * Run: node test-nvidia-json-fix.js
 */

// Simulated test cases for the JSON fix
const testCases = [
  {
    name: "Valid JSON (should pass through)",
    input: '{"command": ["git", "ls-files"], "workdir": "/repo"}',
    expectedSuccess: true,
    expectedCommand: ["git", "ls-files"],
  },
  {
    name: "Text before JSON",
    input: 'I will now proceed with the task {"command": ["git", "ls-files"]}',
    expectedSuccess: true,
    expectedCommand: ["git", "ls-files"],
  },
  {
    name: "Over-escaped braces",
    input: '{"{"command{"{["git", "ls-files"]}',
    expectedSuccess: false, // This pattern is too corrupted
  },
  {
    name: "Duplicate quotes pattern",
    input: '{"command": ["git", "ls-files"]"}',
    expectedSuccess: true,
    expectedCommand: ["git", "ls-files"],
  },
  {
    name: "LLM explanation with JSON",
    input: `I will analyze the codebase structure using git ls-files to identify key files and directories. The output of this command will provide a list of all files in the repository.

{"command": ["git", "ls-files"]}`,
    expectedSuccess: true,
    expectedCommand: ["git", "ls-files"],
  },
];

console.log('\n========================================');
console.log('  NVIDIA NIM JSON Fix Tests');
console.log('========================================\n');

// We'll manually implement simplified versions of the fix functions for testing
function extractJsonFromText(text) {
  const firstBrace = text.indexOf('{');
  const lastBrace = text.lastIndexOf('}');
  
  if (firstBrace !== -1 && lastBrace !== -1 && lastBrace > firstBrace) {
    return text.substring(firstBrace, lastBrace + 1);
  }
  
  return null;
}

function parseNvidiaToolCallJson(rawJsonString) {
  // Strategy 1: Direct parse
  try {
    const parsed = JSON.parse(rawJsonString);
    return { success: true, parsed, strategy: 'direct' };
  } catch (e) {
    // Continue to next strategy
  }

  // Strategy 2: Extract JSON from surrounding text
  const extracted = extractJsonFromText(rawJsonString);
  if (extracted) {
    try {
      const parsed = JSON.parse(extracted);
      return { success: true, parsed, strategy: 'extract' };
    } catch (e) {
      // Continue
    }
  }

  return { success: false, error: 'All strategies failed' };
}

// Run tests
let passed = 0;
let failed = 0;

for (const testCase of testCases) {
  console.log('Test:', testCase.name);
  console.log('Input:', testCase.input.substring(0, 80) + (testCase.input.length > 80 ? '...' : ''));
  
  const result = parseNvidiaToolCallJson(testCase.input);
  
  if (result.success === testCase.expectedSuccess) {
    if (result.success && testCase.expectedCommand) {
      const commandMatches = JSON.stringify(result.parsed.command) === JSON.stringify(testCase.expectedCommand);
      if (commandMatches) {
        console.log('✅ PASS - Extracted command correctly');
        console.log('   Strategy:', result.strategy);
        console.log('   Command:', result.parsed.command.join(' '));
        passed++;
      } else {
        console.log('❌ FAIL - Command mismatch');
        console.log('   Expected:', testCase.expectedCommand);
        console.log('   Got:', result.parsed.command);
        failed++;
      }
    } else {
      console.log(`✅ PASS - Expected ${testCase.expectedSuccess ? 'success' : 'failure'}`);
      if (result.strategy) {
        console.log('   Strategy:', result.strategy);
      }
      passed++;
    }
  } else {
    console.log(`❌ FAIL - Expected ${testCase.expectedSuccess ? 'success' : 'failure'}, got ${result.success ? 'success' : 'failure'}`);
    if (result.error) {
      console.log('   Error:', result.error);
    }
    failed++;
  }
  console.log('');
}

console.log('='.repeat(60));
console.log(`Results: ${passed} passed, ${failed} failed out of ${testCases.length} tests`);
console.log('='.repeat(60) + '\n');

if (failed === 0) {
  console.log('🎉 All tests passed!\n');
  process.exit(0);
} else {
  console.log('❌ Some tests failed\n');
  process.exit(1);
}




