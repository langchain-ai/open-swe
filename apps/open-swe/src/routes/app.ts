import { Webhooks } from "@octokit/webhooks";
import { Hono } from "hono";
import { GitHubApp } from "../utils/github-app.js";
import { createLogger, LogLevel } from "../utils/logger.js";

const logger = createLogger(LogLevel.INFO, "GitHub-Webhook");

export const app = new Hono();

const GITHUB_WEBHOOK_SECRET = process.env.GITHUB_WEBHOOK_SECRET!;

const githubApp = new GitHubApp();

const webhooks = new Webhooks({
  secret: GITHUB_WEBHOOK_SECRET
});

// Handle label added to issue
webhooks.on('issues.labeled', async ({ payload }) => {
  // Check if the added label is 'open-swe'
  if (payload.label?.name !== 'open-swe') {
    console.log(`Label '${payload.label?.name}' added, but not 'open-swe'. Ignoring.`);
    return;
  }

  logger.info(`'open-swe' label added to issue #${payload.issue.number}`);

  try {
    // Get installation ID from the webhook payload
    const installationId = payload.installation?.id;
    
    if (!installationId) {
      logger.error('No installation ID found in webhook payload');
      return;
    }

    // Get authenticated Octokit instance for this installation
    const octokit = await githubApp.getInstallationOctokit(installationId);
    
    // Get installation access token if you need it separately
    const { token, expiresAt } = await githubApp.getInstallationAccessToken(installationId);
    
    logger.info(`Got installation token, expires at: ${expiresAt}`);

    // Extract relevant information from the payload
    const issueData = {
      owner: payload.repository.owner.login,
      repo: payload.repository.name,
      issueNumber: payload.issue.number,
      issueTitle: payload.issue.title,
      issueBody: payload.issue.body || '',
      issueUrl: payload.issue.html_url,
      installationId,
      userId: payload.sender.id,
      userLogin: payload.sender.login
    };

    // Trigger your AI agent workflow
    logger.info("CREATE NEW RUN!!", {
      ...issueData,
      installationToken: token,
      octokit // Pass the authenticated Octokit instance
    })

    logger.info("Creating comment...")
    await octokit.request('POST /repos/{owner}/{repo}/issues/{issue_number}/comments', {
      owner: issueData.owner,
      repo: issueData.repo,
      issue_number: issueData.issueNumber,
      body: '🤖 Open SWE has been triggered for this issue. Processing...'
    });
  } catch (error) {
    logger.error('Error processing webhook:', error);
  }
});


app.post("/webhooks/github", async (c) => {
  logger.info("NEW REQUEST RECEIVED")
  const body = await c.req.text();
  const headers = c.req.header()
  
  try {
    // Verify and process the webhook
    await webhooks.verifyAndReceive({
      id: headers['x-github-delivery'] || '',
      name: headers['x-github-event'] as any,
      signature: headers['x-hub-signature-256'] || '',
      payload: body
    });
    
    return c.json({ received: true });
  } catch (error) {
    logger.error('Webhook error:', error);
    return c.json(
      { error: 'Webhook processing failed' },
      { status: 400 }
    );
  }
});