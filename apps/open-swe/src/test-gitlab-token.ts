/**
 * Test script to verify GitLab token and gitbeaker library
 *
 * This script tests if the GitLab token is working correctly with gitbeaker
 * by attempting to:
 * 1. Verify the token
 * 2. Fetch a project
 * 3. Create a test issue
 */

import { Gitlab } from "@gitbeaker/rest";
import { decryptSecret } from "@openswe/shared/crypto";

// Get the encrypted token and encryption key from environment
const ENCRYPTED_GITLAB_TOKEN = process.env.ENCRYPTED_GITLAB_TOKEN;
const ENCRYPTION_KEY = process.env.SECRETS_ENCRYPTION_KEY;
const GITLAB_BASE_URL = process.env.GITLAB_BASE_URL || "https://gitlab.com";

// Project to test
const OWNER = process.env.TEST_OWNER || "marco316";
const REPO = process.env.TEST_REPO || "my-gitlab-project";

async function testGitLabToken() {
  console.log("\n=== Testing GitLab Token with Gitbeaker ===\n");

  // Decrypt the token
  if (!ENCRYPTED_GITLAB_TOKEN) {
    console.error("❌ ENCRYPTED_GITLAB_TOKEN environment variable not set");
    console.error("\nUsage: ENCRYPTED_GITLAB_TOKEN=<encrypted_token> SECRETS_ENCRYPTION_KEY=<key> tsx src/test-gitlab-token.ts");
    process.exit(1);
  }

  if (!ENCRYPTION_KEY) {
    console.error("❌ SECRETS_ENCRYPTION_KEY environment variable not set");
    process.exit(1);
  }

  console.log(`Encrypted token (first 20): ${ENCRYPTED_GITLAB_TOKEN.substring(0, 20)}...`);

  let decryptedToken: string;
  try {
    decryptedToken = decryptSecret(ENCRYPTED_GITLAB_TOKEN, ENCRYPTION_KEY);
    console.log(`✅ Token decrypted successfully`);
    console.log(`Decrypted token (first 10): ${decryptedToken.substring(0, 10)}...\n`);
  } catch (error: any) {
    console.error("❌ Failed to decrypt token:", error.message);
    process.exit(1);
  }

  // Initialize gitbeaker
  console.log(`Base URL: ${GITLAB_BASE_URL}`);
  console.log(`Testing project: ${OWNER}/${REPO}\n`);

  const gitlab = new Gitlab({
    token: decryptedToken,
    host: GITLAB_BASE_URL,
  });

  try {
    // Test 1: Verify token
    console.log("Test 1: Verifying token...");
    const user = await gitlab.Users.showCurrentUser();
    console.log(`✅ Token valid! User: ${user.username} (ID: ${user.id})`);
    console.log(`   Name: ${user.name}`);
    console.log(`   Email: ${user.email || 'N/A'}\n`);

    // Test 2: Fetch project
    console.log("Test 2: Fetching project...");
    const projectId = `${OWNER}/${REPO}`;
    const project = await gitlab.Projects.show(projectId);
    console.log(`✅ Project found!`);
    console.log(`   Name: ${project.name}`);
    console.log(`   ID: ${project.id}`);
    console.log(`   Path: ${project.path_with_namespace}`);
    console.log(`   Default branch: ${project.default_branch}`);
    console.log(`   Visibility: ${project.visibility}`);
    console.log(`   Issues enabled: ${project.issues_enabled !== false ? 'Yes' : 'No'}\n`);

    // Test 3: Check permissions
    console.log("Test 3: Checking permissions...");
    const permissions = project.permissions as any;
    console.log(`   Project access level: ${permissions?.project_access?.access_level || 'N/A'}`);
    console.log(`   Group access level: ${permissions?.group_access?.access_level || 'N/A'}`);

    // Access levels: 10 (Guest), 20 (Reporter), 30 (Developer), 40 (Maintainer), 50 (Owner)
    const hasIssueCreateAccess =
      (permissions?.project_access?.access_level >= 20) ||
      (permissions?.group_access?.access_level >= 20);
    console.log(`   Can create issues: ${hasIssueCreateAccess ? 'Yes' : 'No (need at least Reporter role)'}\n`);

    // Test 4: List existing issues (read-only test)
    console.log("Test 4: Listing existing issues (read-only)...");
    const issues = await gitlab.Issues.all({ projectId, maxPages: 1, perPage: 5 });
    console.log(`✅ Successfully fetched issues (found ${Array.isArray(issues) ? issues.length : 0} issues)\n`);

    // Test 5: Create a test issue
    console.log("Test 5: Creating a test issue...");
    const testIssue = await gitlab.Issues.create(
      projectId,
      "Test Issue - Gitbeaker Verification",
      {
        description: `This is a test issue created to verify the gitbeaker library is working correctly.

**Test Details:**
- Created by: ${user.username}
- User ID: ${user.id}
- Timestamp: ${new Date().toISOString()}
- Project: ${project.path_with_namespace}

You can safely close or delete this issue.`,
        labels: "test",
      }
    );
    console.log(`✅ Issue created successfully!`);
    console.log(`   Issue #${testIssue.iid}: ${testIssue.title}`);
    console.log(`   URL: ${testIssue.web_url}`);
    console.log(`   State: ${testIssue.state}\n`);

    console.log("=== ✅ All Tests Passed! ===\n");
    console.log("✅ The gitbeaker library is working correctly with your token.");
    console.log("✅ The token has proper permissions to create issues.");
    console.log("\nConclusion: The issue must be in how the token is being passed in the application flow.\n");

  } catch (error: any) {
    console.error("\n=== ❌ Test Failed! ===\n");
    console.error("Error:", error.message);

    if (error.response) {
      console.error("\nHTTP Response:");
      console.error(`  Status: ${error.response.status} ${error.response.statusText}`);
      console.error(`  Data:`, JSON.stringify(error.response.data, null, 2));
    }

    if (error.cause) {
      console.error("\nCause:", error.cause);
    }

    console.error("\n=== Diagnosis ===");

    if (error.message.includes("401") || error.response?.status === 401) {
      console.error("\n❌ 401 Unauthorized Error\n");
      console.error("Possible causes:");
      console.error("  1. Token is invalid or expired");
      console.error("  2. Token doesn't have required scopes (needs 'api' scope)");
      console.error("  3. Token format is incorrect");
      console.error("\nHow to fix:");
      console.error("  1. Go to GitLab > User Settings > Access Tokens");
      console.error("  2. Create a new Personal Access Token");
      console.error("  3. Select the 'api' scope (full API access)");
      console.error("  4. Update your token in the application");
      console.error("\nToken scopes needed:");
      console.error("  - api (for full API access including issue creation)");
      console.error("  OR");
      console.error("  - read_api + write_repository");
    } else if (error.message.includes("404") || error.response?.status === 404) {
      console.error("\n❌ 404 Not Found Error\n");
      console.error("Possible causes:");
      console.error("  1. Project doesn't exist");
      console.error("  2. Token doesn't have access to the project");
      console.error(`  3. Project path '${OWNER}/${REPO}' is incorrect`);
      console.error("\nHow to fix:");
      console.error(`  1. Verify the project exists at: ${GITLAB_BASE_URL}/${OWNER}/${REPO}`);
      console.error("  2. Check if your user has access to the project");
      console.error("  3. Ensure the project is not private or you have been granted access");
    } else if (error.message.includes("403") || error.response?.status === 403) {
      console.error("\n❌ 403 Forbidden Error\n");
      console.error("Possible causes:");
      console.error("  1. User doesn't have permission to create issues in this project");
      console.error("  2. Issues are disabled for this project");
      console.error("  3. Project requires at least 'Reporter' role to create issues");
      console.error("\nHow to fix:");
      console.error("  1. Ask a project maintainer to grant you 'Reporter' or higher role");
      console.error("  2. Verify issues are enabled in Project Settings > General > Visibility");
      console.error("  3. Check if there are any branch protection rules blocking the action");
    }

    process.exit(1);
  }
}

// Run the test
testGitLabToken().catch((error) => {
  console.error("\n❌ Unexpected error:", error);
  process.exit(1);
});
