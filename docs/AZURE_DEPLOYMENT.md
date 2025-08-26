# Azure Deployment Guide for Agent Mojo

This guide provides step-by-step instructions for deploying Agent Mojo to Azure App Service without Docker, supporting multiple environments (development, staging, production).

## Prerequisites

- Azure CLI installed and configured
- Azure subscription with appropriate permissions
- Node.js 18+ installed locally
- Git repository with Agent Mojo codebase
- GitHub repository for CI/CD (optional but recommended)

## Architecture Overview

The deployment consists of:
- **Web App**: Next.js frontend application (`apps/web`)
- **Agent App**: LangGraph agent backend (`apps/agent-mojo`)
- **Storage Account**: For file storage and logs
- **App Service Plans**: Hosting infrastructure for both apps

## Quick Start

### 1. Clone and Setup

```bash
git clone <your-repo-url>
cd agent-mojo
```

### 2. Configure Environment

Edit `azure-env-config.json` with your specific values:

```bash
cp azure-env-config.json azure-env-config.local.json
# Edit azure-env-config.local.json with your values
```

### 3. Run Setup Script

**Using PowerShell (Windows):**
```powershell
.\scripts\setup-azure.ps1 -Environment "development" -SubscriptionId "your-subscription-id"
```

**Using Bash (macOS/Linux):**
```bash
./scripts/setup-azure.sh -e development -s your-subscription-id
```

## Manual Setup

### Step 1: Create Resource Groups

```bash
# Set your subscription
az account set --subscription "your-subscription-id"

# Create resource groups
az group create --name "agent-mojo-dev-web-rg" --location "eastus"
az group create --name "agent-mojo-dev-agent-rg" --location "eastus"
```

### Step 2: Create App Service Plans

```bash
# Web app service plan
az appservice plan create \
    --name "agent-mojo-dev-web-plan" \
    --resource-group "agent-mojo-dev-web-rg" \
    --location "eastus" \
    --sku B1 \
    --is-linux

# Agent app service plan
az appservice plan create \
    --name "agent-mojo-dev-agent-plan" \
    --resource-group "agent-mojo-dev-agent-rg" \
    --location "eastus" \
    --sku B1 \
    --is-linux
```

### Step 3: Create Web Apps

```bash
# Web application
az webapp create \
    --name "agent-mojo-dev-web" \
    --resource-group "agent-mojo-dev-web-rg" \
    --plan "agent-mojo-dev-web-plan" \
    --runtime "NODE|18-lts"

# Agent application
az webapp create \
    --name "agent-mojo-dev-agent" \
    --resource-group "agent-mojo-dev-agent-rg" \
    --plan "agent-mojo-dev-agent-plan" \
    --runtime "NODE|18-lts"
```

### Step 4: Configure Environment Variables

```bash
# Web app environment variables
az webapp config appsettings set \
    --name "agent-mojo-dev-web" \
    --resource-group "agent-mojo-dev-web-rg" \
    --settings \
        NODE_ENV="development" \
        NEXT_PUBLIC_API_URL="https://agent-mojo-dev-agent.azurewebsites.net" \
        NEXTAUTH_URL="https://agent-mojo-dev-web.azurewebsites.net" \
        NEXTAUTH_SECRET="your-nextauth-secret"

# Agent app environment variables
az webapp config appsettings set \
    --name "agent-mojo-dev-agent" \
    --resource-group "agent-mojo-dev-agent-rg" \
    --settings \
        NODE_ENV="development" \
        PORT="8000" \
        OPENAI_API_KEY="your-openai-key" \
        ANTHROPIC_API_KEY="your-anthropic-key"
```

### Step 5: Configure Startup Commands

```bash
# Web app startup
az webapp config set \
    --name "agent-mojo-dev-web" \
    --resource-group "agent-mojo-dev-web-rg" \
    --startup-file "apps/web/startup.sh"

# Agent app startup
az webapp config set \
    --name "agent-mojo-dev-agent" \
    --resource-group "agent-mojo-dev-agent-rg" \
    --startup-file "apps/agent-mojo/startup.sh"
```

## Environment Configuration

### Development Environment
- **Web App**: `agent-mojo-dev-web.azurewebsites.net`
- **Agent App**: `agent-mojo-dev-agent.azurewebsites.net`
- **Branch**: `develop`
- **Auto-deploy**: On push to `develop`

### Staging Environment
- **Web App**: `agent-mojo-staging-web.azurewebsites.net`
- **Agent App**: `agent-mojo-staging-agent.azurewebsites.net`
- **Branch**: `staging`
- **Auto-deploy**: On push to `staging`

### Production Environment
- **Web App**: `agent-mojo-prod-web.azurewebsites.net`
- **Agent App**: `agent-mojo-prod-agent.azurewebsites.net`
- **Branch**: `main`
- **Auto-deploy**: On push to `main` (with approval)

## CI/CD with GitHub Actions

### Setup GitHub Secrets

Add these secrets to your GitHub repository:

**Development:**
- `AZURE_WEBAPP_NAME_DEV`
- `AZURE_AGENT_NAME_DEV`
- `AZURE_WEBAPP_PUBLISH_PROFILE_DEV`
- `AZURE_AGENT_PUBLISH_PROFILE_DEV`

**Staging:**
- `AZURE_WEBAPP_NAME_STAGING`
- `AZURE_AGENT_NAME_STAGING`
- `AZURE_WEBAPP_PUBLISH_PROFILE_STAGING`
- `AZURE_AGENT_PUBLISH_PROFILE_STAGING`

**Production:**
- `AZURE_WEBAPP_NAME_PROD`
- `AZURE_AGENT_NAME_PROD`
- `AZURE_WEBAPP_PUBLISH_PROFILE_PROD`
- `AZURE_AGENT_PUBLISH_PROFILE_PROD`

### Get Publish Profiles

```bash
# Get web app publish profile
az webapp deployment list-publishing-profiles \
    --name "agent-mojo-dev-web" \
    --resource-group "agent-mojo-dev-web-rg" \
    --xml

# Get agent app publish profile
az webapp deployment list-publishing-profiles \
    --name "agent-mojo-dev-agent" \
    --resource-group "agent-mojo-dev-agent-rg" \
    --xml
```

## Environment Variables Reference

### Web App Required Variables

| Variable | Description | Example |
|----------|-------------|----------|
| `NODE_ENV` | Environment mode | `development`, `staging`, `production` |
| `NEXT_PUBLIC_API_URL` | Agent API URL | `https://agent-mojo-dev-agent.azurewebsites.net` |
| `NEXTAUTH_URL` | Web app URL | `https://agent-mojo-dev-web.azurewebsites.net` |
| `NEXTAUTH_SECRET` | NextAuth secret key | `your-secret-key` |
| `GITHUB_APP_ID` | GitHub App ID | `123456` |
| `GITHUB_APP_PRIVATE_KEY` | GitHub App private key | `-----BEGIN PRIVATE KEY-----...` |

### Agent App Required Variables

| Variable | Description | Example |
|----------|-------------|----------|
| `NODE_ENV` | Environment mode | `development`, `staging`, `production` |
| `PORT` | Server port | `8000` |
| `OPENAI_API_KEY` | OpenAI API key | `sk-...` |
| `ANTHROPIC_API_KEY` | Anthropic API key | `sk-ant-...` |
| `LANGCHAIN_API_KEY` | LangChain API key | `ls__...` |
| `LANGCHAIN_PROJECT` | LangChain project name | `agent-mojo-dev` |

### Optional Variables

| Variable | Description | Default |
|----------|-------------|----------|
| `AZURE_STORAGE_CONNECTION_STRING` | Storage connection | Auto-generated |
| `LOG_LEVEL` | Logging level | `info` |
| `MAX_CONCURRENT_REQUESTS` | Request limit | `10` |

## Deployment Commands

### Manual Deployment

```bash
# Build and deploy web app
cd apps/web
npm run build
az webapp deployment source config-zip \
    --name "agent-mojo-dev-web" \
    --resource-group "agent-mojo-dev-web-rg" \
    --src "web-app.zip"

# Build and deploy agent app
cd ../agent-mojo
npm run build
az webapp deployment source config-zip \
    --name "agent-mojo-dev-agent" \
    --resource-group "agent-mojo-dev-agent-rg" \
    --src "agent-app.zip"
```

### GitHub Actions Deployment

Deployment is automatic when you push to the configured branches:

```bash
# Deploy to development
git push origin develop

# Deploy to staging
git push origin staging

# Deploy to production
git push origin main
```

## Monitoring and Logs

### Enable Application Insights

```bash
# Create Application Insights
az monitor app-insights component create \
    --app "agent-mojo-dev-insights" \
    --location "eastus" \
    --resource-group "agent-mojo-dev-web-rg"

# Get instrumentation key
az monitor app-insights component show \
    --app "agent-mojo-dev-insights" \
    --resource-group "agent-mojo-dev-web-rg" \
    --query instrumentationKey
```

### View Logs

```bash
# Stream web app logs
az webapp log tail \
    --name "agent-mojo-dev-web" \
    --resource-group "agent-mojo-dev-web-rg"

# Stream agent app logs
az webapp log tail \
    --name "agent-mojo-dev-agent" \
    --resource-group "agent-mojo-dev-agent-rg"
```

### Access Logs via Portal

1. Go to [Azure Portal](https://portal.azure.com)
2. Navigate to your App Service
3. Select "Log stream" or "Logs" from the left menu

## Troubleshooting

### Common Issues

**1. App won't start**
- Check startup script permissions: `chmod +x apps/web/startup.sh`
- Verify Node.js version compatibility
- Check environment variables are set correctly

**2. Build failures**
- Ensure all dependencies are in `package.json`
- Check for missing environment variables during build
- Verify build scripts in `package.json`

**3. Connection issues**
- Verify CORS settings if needed
- Check firewall rules
- Ensure URLs in environment variables are correct

**4. Performance issues**
- Consider upgrading App Service Plan (B1 → S1 → P1V2)
- Enable Application Insights for monitoring
- Check for memory leaks in logs

### Debug Commands

```bash
# Check app status
az webapp show \
    --name "agent-mojo-dev-web" \
    --resource-group "agent-mojo-dev-web-rg" \
    --query state

# Restart app
az webapp restart \
    --name "agent-mojo-dev-web" \
    --resource-group "agent-mojo-dev-web-rg"

# Check environment variables
az webapp config appsettings list \
    --name "agent-mojo-dev-web" \
    --resource-group "agent-mojo-dev-web-rg"
```

## Security Considerations

1. **Environment Variables**: Never commit secrets to version control
2. **HTTPS**: Always use HTTPS in production (enabled by default)
3. **Authentication**: Configure proper authentication for your GitHub App
4. **Network Security**: Consider using Virtual Networks for production
5. **Access Control**: Use Azure RBAC to limit access to resources

## Cost Optimization

1. **App Service Plan**: Start with B1, scale up as needed
2. **Auto-scaling**: Configure based on CPU/memory usage
3. **Development**: Use shared plans for non-production environments
4. **Storage**: Use appropriate storage tiers
5. **Monitoring**: Set up cost alerts

## Next Steps

1. Set up Application Insights for monitoring
2. Configure custom domains and SSL certificates
3. Implement blue-green deployments for zero-downtime updates
4. Set up automated backups
5. Configure disaster recovery

## Support

For issues with this deployment:
1. Check the troubleshooting section above
2. Review Azure App Service documentation
3. Check GitHub Actions logs for CI/CD issues
4. Contact your Azure support team for infrastructure issues

---

**Note**: This guide assumes you're deploying without Docker. If you prefer containerized deployments, consider using Azure Container Instances or Azure Container Apps instead.