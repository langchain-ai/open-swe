import { createLogger, LogLevel } from "../../src/utils/logger.js";

const logger = createLogger(LogLevel.INFO, "DeployLangGraph");

interface DeploymentConfig {
  controlPlaneHost: string;
  langsmithApiKey: string;
  integrationId: string;
  deploymentId?: string;
}

interface DeploymentResponse {
  id: string;
  latest_revision_id: string;
}

interface RevisionResponse {
  id: string;
  status: string;
}

interface RevisionsListResponse {
  resources: RevisionResponse[];
}

const MAX_WAIT_TIME = 1800; // 30 minutes
const POLL_INTERVAL = 60; // 60 seconds

function getRequiredEnvVar(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Required environment variable ${name} is not set`);
  }
  return value;
}

function getDeploymentConfig(): DeploymentConfig {
  return {
    controlPlaneHost: getRequiredEnvVar("CONTROL_PLANE_HOST"),
    langsmithApiKey: getRequiredEnvVar("LANGSMITH_API_KEY"),
    integrationId: getRequiredEnvVar("INTEGRATION_ID"),
    deploymentId: process.env.DEPLOYMENT_ID,
  };
}

function getHeaders(apiKey: string, includeContentType = false): HeadersInit {
  const headers: HeadersInit = {
    "X-Api-Key": apiKey,
  };
  
  if (includeContentType) {
    headers["Content-Type"] = "application/json";
  }
  
  return headers;
}

async function makeRequest<T>(
  url: string,
  options: RequestInit,
  expectedStatus: number
): Promise<T> {
  try {
    const response = await fetch(url, options);
    
    if (response.status !== expectedStatus) {
      const errorText = await response.text();
      throw new Error(
        `Request failed with status ${response.status}: ${errorText}`
      );
    }
    
    if (expectedStatus === 204) {
      return {} as T;
    }
    
    return await response.json();
  } catch (error) {
    if (error instanceof Error) {
      throw new Error(`HTTP request failed: ${error.message}`);
    }
    throw new Error("HTTP request failed with unknown error");
  }
}

async function getDeployment(
  config: DeploymentConfig,
  deploymentId: string
): Promise<DeploymentResponse> {
  logger.info(`Getting deployment ${deploymentId}`);
  
  const url = `${config.controlPlaneHost}/v2/deployments/${deploymentId}`;
  const options: RequestInit = {
    method: "GET",
    headers: getHeaders(config.langsmithApiKey),
  };
  
  return makeRequest<DeploymentResponse>(url, options, 200);
}

async function listRevisions(
  config: DeploymentConfig,
  deploymentId: string
): Promise<RevisionsListResponse> {
  logger.info(`Listing revisions for deployment ${deploymentId}`);
  
  const url = `${config.controlPlaneHost}/v2/deployments/${deploymentId}/revisions`;
  const options: RequestInit = {
    method: "GET",
    headers: getHeaders(config.langsmithApiKey),
  };
  
  return makeRequest<RevisionsListResponse>(url, options, 200);
}

async function getRevision(
  config: DeploymentConfig,
  deploymentId: string,
  revisionId: string
): Promise<RevisionResponse> {
  const url = `${config.controlPlaneHost}/v2/deployments/${deploymentId}/revisions/${revisionId}`;
  const options: RequestInit = {
    method: "GET",
    headers: getHeaders(config.langsmithApiKey),
  };
  
  return makeRequest<RevisionResponse>(url, options, 200);
}

async function patchDeployment(
  config: DeploymentConfig,
  deploymentId: string
): Promise<void> {
  logger.info(`Patching deployment ${deploymentId} to trigger new revision`);
  
  const url = `${config.controlPlaneHost}/v2/deployments/${deploymentId}`;
  const requestBody = {
    source_revision_config: {
      repo_ref: "main",
      langgraph_config_path: "langgraph.json",
    },
  };
  
  const options: RequestInit = {
    method: "PATCH",
    headers: getHeaders(config.langsmithApiKey, true),
    body: JSON.stringify(requestBody),
  };
  
  await makeRequest<void>(url, options, 200);
  logger.info(`Successfully patched deployment ${deploymentId}`);
}

async function waitForDeployment(
  config: DeploymentConfig,
  deploymentId: string,
  revisionId: string
): Promise<void> {
  logger.info(`Waiting for revision ${revisionId} to be deployed`);
  
  const startTime = Date.now();
  
  while (Date.now() - startTime < MAX_WAIT_TIME * 1000) {
    const revision = await getRevision(config, deploymentId, revisionId);
    const status = revision.status;
    
    logger.info(`Revision ${revisionId} status: ${status}`);
    
    if (status === "DEPLOYED") {
      logger.info(`Revision ${revisionId} successfully deployed`);
      return;
    }
    
    if (status.includes("FAILED")) {
      throw new Error(`Revision ${revisionId} failed with status: ${status}`);
    }
    
    logger.info(`Waiting ${POLL_INTERVAL} seconds before next status check...`);
    await new Promise(resolve => setTimeout(resolve, POLL_INTERVAL * 1000));
  }
  
  throw new Error(
    `Timeout waiting for revision ${revisionId} to be deployed after ${MAX_WAIT_TIME} seconds`
  );
}

async function deployLangGraph(): Promise<void> {
  try {
    logger.info("Starting LangGraph deployment process");
    
    const config = getDeploymentConfig();
    
    if (!config.deploymentId) {
      throw new Error("DEPLOYMENT_ID environment variable is required");
    }
    
    // Verify deployment exists
    await getDeployment(config, config.deploymentId);
    
    // Patch deployment to trigger new revision
    await patchDeployment(config, config.deploymentId);
    
    // Get the latest revision after patching
    const revisions = await listRevisions(config, config.deploymentId);
    const latestRevision = revisions.resources[0];
    
    if (!latestRevision) {
      throw new Error("No revisions found for deployment");
    }
    
    // Wait for the new revision to be deployed
    await waitForDeployment(config, config.deploymentId, latestRevision.id);
    
    logger.info("LangGraph deployment completed successfully");
  } catch (error) {
    logger.error("LangGraph deployment failed", { error: error instanceof Error ? error.message : String(error) });
    process.exit(1);
  }
}

// Execute deployment if this script is run directly
if (import.meta.url === `file://${process.argv[1]}`) {
  deployLangGraph();
}

