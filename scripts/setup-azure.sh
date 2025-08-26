#!/bin/bash

# Azure App Service Setup Script for Agent Mojo
# This script creates Azure App Services and configures environment variables
# Prerequisites: Azure CLI installed and logged in

set -e

# Function to display usage
usage() {
    echo "Usage: $0 -e <environment> -s <subscription-id> [-l <location>]"
    echo "  -e: Environment (development, staging, production)"
    echo "  -s: Azure Subscription ID"
    echo "  -l: Azure Location (default: eastus)"
    exit 1
}

# Parse command line arguments
ENVIRONMENT=""
SUBSCRIPTION_ID=""
LOCATION="eastus"

while getopts "e:s:l:h" opt; do
    case $opt in
        e) ENVIRONMENT="$OPTARG" ;;
        s) SUBSCRIPTION_ID="$OPTARG" ;;
        l) LOCATION="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

# Validate required parameters
if [[ -z "$ENVIRONMENT" || -z "$SUBSCRIPTION_ID" ]]; then
    echo "Error: Environment and Subscription ID are required"
    usage
fi

# Validate environment
if [[ ! "$ENVIRONMENT" =~ ^(development|staging|production)$ ]]; then
    echo "Error: Environment must be one of: development, staging, production"
    exit 1
fi

# Load configuration
CONFIG_PATH="$(dirname "$0")/../azure-env-config.json"
if [[ ! -f "$CONFIG_PATH" ]]; then
    echo "Error: Configuration file not found: $CONFIG_PATH"
    exit 1
fi

echo "Setting up Azure resources for environment: $ENVIRONMENT"

# Set subscription
echo "Setting Azure subscription..."
az account set --subscription "$SUBSCRIPTION_ID"

# Extract configuration using jq
WEB_APP_NAME=$(jq -r ".environments.$ENVIRONMENT.web_app.app_name" "$CONFIG_PATH")
AGENT_APP_NAME=$(jq -r ".environments.$ENVIRONMENT.agent_app.app_name" "$CONFIG_PATH")
WEB_RESOURCE_GROUP=$(jq -r ".environments.$ENVIRONMENT.web_app.resource_group" "$CONFIG_PATH")
AGENT_RESOURCE_GROUP=$(jq -r ".environments.$ENVIRONMENT.agent_app.resource_group" "$CONFIG_PATH")

if [[ "$WEB_APP_NAME" == "null" || "$AGENT_APP_NAME" == "null" ]]; then
    echo "Error: Environment '$ENVIRONMENT' not found in configuration"
    exit 1
fi

echo "Web App: $WEB_APP_NAME"
echo "Agent App: $AGENT_APP_NAME"
echo "Location: $LOCATION"

# Create resource groups
echo "Creating resource groups..."
az group create --name "$WEB_RESOURCE_GROUP" --location "$LOCATION"
az group create --name "$AGENT_RESOURCE_GROUP" --location "$LOCATION"

# Create App Service Plans
WEB_APP_PLAN="${WEB_APP_NAME}-plan"
AGENT_APP_PLAN="${AGENT_APP_NAME}-plan"

echo "Creating App Service Plans..."
az appservice plan create \
    --name "$WEB_APP_PLAN" \
    --resource-group "$WEB_RESOURCE_GROUP" \
    --location "$LOCATION" \
    --sku B1 \
    --is-linux

az appservice plan create \
    --name "$AGENT_APP_PLAN" \
    --resource-group "$AGENT_RESOURCE_GROUP" \
    --location "$LOCATION" \
    --sku B1 \
    --is-linux

# Create Web Apps
echo "Creating Web Apps..."
az webapp create \
    --name "$WEB_APP_NAME" \
    --resource-group "$WEB_RESOURCE_GROUP" \
    --plan "$WEB_APP_PLAN" \
    --runtime "NODE|18-lts"

az webapp create \
    --name "$AGENT_APP_NAME" \
    --resource-group "$AGENT_RESOURCE_GROUP" \
    --plan "$AGENT_APP_PLAN" \
    --runtime "NODE|18-lts"

# Configure Web App environment variables
echo "Configuring Web App environment variables..."

# Extract and set web app environment variables
WEB_ENV_VARS=$(jq -r ".environments.$ENVIRONMENT.web_app.environment_variables | to_entries | map(\"\(.key)=\(.value)\") | join(\" \")" "$CONFIG_PATH")
if [[ -n "$WEB_ENV_VARS" ]]; then
    az webapp config appsettings set \
        --name "$WEB_APP_NAME" \
        --resource-group "$WEB_RESOURCE_GROUP" \
        --settings $WEB_ENV_VARS
fi

# Extract and set agent app environment variables
AGENT_ENV_VARS=$(jq -r ".environments.$ENVIRONMENT.agent_app.environment_variables | to_entries | map(\"\(.key)=\(.value)\") | join(\" \")" "$CONFIG_PATH")
if [[ -n "$AGENT_ENV_VARS" ]]; then
    az webapp config appsettings set \
        --name "$AGENT_APP_NAME" \
        --resource-group "$AGENT_RESOURCE_GROUP" \
        --settings $AGENT_ENV_VARS
fi

# Configure startup commands
echo "Configuring startup commands..."
az webapp config set \
    --name "$WEB_APP_NAME" \
    --resource-group "$WEB_RESOURCE_GROUP" \
    --startup-file "apps/web/startup.sh"

az webapp config set \
    --name "$AGENT_APP_NAME" \
    --resource-group "$AGENT_RESOURCE_GROUP" \
    --startup-file "apps/agent-mojo/startup.sh"

# Enable logging
echo "Enabling application logging..."
az webapp log config \
    --name "$WEB_APP_NAME" \
    --resource-group "$WEB_RESOURCE_GROUP" \
    --application-logging filesystem \
    --level information

az webapp log config \
    --name "$AGENT_APP_NAME" \
    --resource-group "$AGENT_RESOURCE_GROUP" \
    --application-logging filesystem \
    --level information

# Create Azure Storage Account (if needed)
STORAGE_ACCOUNT_NAME="agentmojo${ENVIRONMENT}storage"
echo "Creating Storage Account: $STORAGE_ACCOUNT_NAME"

az storage account create \
    --name "$STORAGE_ACCOUNT_NAME" \
    --resource-group "$WEB_RESOURCE_GROUP" \
    --location "$LOCATION" \
    --sku Standard_LRS \
    --kind StorageV2

# Get connection string
CONNECTION_STRING=$(az storage account show-connection-string \
    --name "$STORAGE_ACCOUNT_NAME" \
    --resource-group "$WEB_RESOURCE_GROUP" \
    --query connectionString \
    --output tsv)

echo "Storage Account Connection String: $CONNECTION_STRING"

# Get publish profiles for GitHub Actions
echo "Getting publish profiles for GitHub Actions..."
WEB_PUBLISH_PROFILE=$(az webapp deployment list-publishing-profiles \
    --name "$WEB_APP_NAME" \
    --resource-group "$WEB_RESOURCE_GROUP" \
    --xml)

AGENT_PUBLISH_PROFILE=$(az webapp deployment list-publishing-profiles \
    --name "$AGENT_APP_NAME" \
    --resource-group "$AGENT_RESOURCE_GROUP" \
    --xml)

# Output deployment information
echo ""
echo "=== Deployment Information ==="
echo "Web App URL: https://${WEB_APP_NAME}.azurewebsites.net"
echo "Agent App URL: https://${AGENT_APP_NAME}.azurewebsites.net"
echo ""
echo "=== GitHub Secrets to Configure ==="
echo "Add these secrets to your GitHub repository:"
echo ""
case $ENVIRONMENT in
    "development")
        echo "AZURE_WEBAPP_NAME_DEV: $WEB_APP_NAME"
        echo "AZURE_AGENT_NAME_DEV: $AGENT_APP_NAME"
        echo "AZURE_WEBAPP_PUBLISH_PROFILE_DEV: <publish profile content>"
        echo "AZURE_AGENT_PUBLISH_PROFILE_DEV: <publish profile content>"
        ;;
    "staging")
        echo "AZURE_WEBAPP_NAME_STAGING: $WEB_APP_NAME"
        echo "AZURE_AGENT_NAME_STAGING: $AGENT_APP_NAME"
        echo "AZURE_WEBAPP_PUBLISH_PROFILE_STAGING: <publish profile content>"
        echo "AZURE_AGENT_PUBLISH_PROFILE_STAGING: <publish profile content>"
        ;;
    "production")
        echo "AZURE_WEBAPP_NAME_PROD: $WEB_APP_NAME"
        echo "AZURE_AGENT_NAME_PROD: $AGENT_APP_NAME"
        echo "AZURE_WEBAPP_PUBLISH_PROFILE_PROD: <publish profile content>"
        echo "AZURE_AGENT_PUBLISH_PROFILE_PROD: <publish profile content>"
        ;;
esac

echo ""
echo "=== Next Steps ==="
echo "1. Copy the publish profiles above and add them to your GitHub repository secrets"
echo "2. Replace placeholder values (\${...}) in environment variables with actual values"
echo "3. Configure your GitHub App settings with the new URLs"
echo "4. Test the deployment by pushing to the appropriate branch"
echo ""
echo "Setup completed successfully!"