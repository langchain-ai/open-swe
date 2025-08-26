# Azure App Service Setup Script for Agent Mojo
# This script creates Azure App Services and configures environment variables
# Prerequisites: Azure CLI installed and logged in

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("development", "staging", "production")]
    [string]$Environment,
    
    [Parameter(Mandatory=$true)]
    [string]$SubscriptionId,
    
    [Parameter(Mandatory=$true)]
    [string]$Location = "East US"
)

# Load configuration
$configPath = "../azure-env-config.json"
if (-not (Test-Path $configPath)) {
    Write-Error "Configuration file not found: $configPath"
    exit 1
}

$config = Get-Content $configPath | ConvertFrom-Json
$envConfig = $config.environments.$Environment

if (-not $envConfig) {
    Write-Error "Environment '$Environment' not found in configuration"
    exit 1
}

Write-Host "Setting up Azure resources for environment: $Environment" -ForegroundColor Green

# Set subscription
az account set --subscription $SubscriptionId

# Create resource groups
$webResourceGroup = $envConfig.web_app.resource_group
$agentResourceGroup = $envConfig.agent_app.resource_group

Write-Host "Creating resource groups..." -ForegroundColor Yellow
az group create --name $webResourceGroup --location $Location
az group create --name $agentResourceGroup --location $Location

# Create App Service Plans
$webAppPlan = "$($envConfig.web_app.app_name)-plan"
$agentAppPlan = "$($envConfig.agent_app.app_name)-plan"

Write-Host "Creating App Service Plans..." -ForegroundColor Yellow
az appservice plan create `
    --name $webAppPlan `
    --resource-group $webResourceGroup `
    --location $Location `
    --sku B1 `
    --is-linux

az appservice plan create `
    --name $agentAppPlan `
    --resource-group $agentResourceGroup `
    --location $Location `
    --sku B1 `
    --is-linux

# Create Web Apps
Write-Host "Creating Web Apps..." -ForegroundColor Yellow
az webapp create `
    --name $envConfig.web_app.app_name `
    --resource-group $webResourceGroup `
    --plan $webAppPlan `
    --runtime "NODE|18-lts"

az webapp create `
    --name $envConfig.agent_app.app_name `
    --resource-group $agentResourceGroup `
    --plan $agentAppPlan `
    --runtime "NODE|18-lts"

# Configure Web App settings
Write-Host "Configuring Web App environment variables..." -ForegroundColor Yellow

# Web App environment variables
$webAppSettings = @()
foreach ($key in $envConfig.web_app.environment_variables.PSObject.Properties.Name) {
    $value = $envConfig.web_app.environment_variables.$key
    $webAppSettings += "$key=$value"
}

az webapp config appsettings set `
    --name $envConfig.web_app.app_name `
    --resource-group $webResourceGroup `
    --settings $webAppSettings

# Agent App environment variables
$agentAppSettings = @()
foreach ($key in $envConfig.agent_app.environment_variables.PSObject.Properties.Name) {
    $value = $envConfig.agent_app.environment_variables.$key
    $agentAppSettings += "$key=$value"
}

az webapp config appsettings set `
    --name $envConfig.agent_app.app_name `
    --resource-group $agentResourceGroup `
    --settings $agentAppSettings

# Configure startup commands
Write-Host "Configuring startup commands..." -ForegroundColor Yellow
az webapp config set `
    --name $envConfig.web_app.app_name `
    --resource-group $webResourceGroup `
    --startup-file "apps/web/startup.sh"

az webapp config set `
    --name $envConfig.agent_app.app_name `
    --resource-group $agentResourceGroup `
    --startup-file "apps/agent-mojo/startup.sh"

# Enable logging
Write-Host "Enabling application logging..." -ForegroundColor Yellow
az webapp log config `
    --name $envConfig.web_app.app_name `
    --resource-group $webResourceGroup `
    --application-logging filesystem `
    --level information

az webapp log config `
    --name $envConfig.agent_app.app_name `
    --resource-group $agentResourceGroup `
    --application-logging filesystem `
    --level information

# Create Azure Storage Account (if needed)
if ($config.azure_services.required_services -contains "Azure Storage Account (for file storage)") {
    $storageAccountName = "agentmojo$($Environment.ToLower())storage"
    Write-Host "Creating Storage Account: $storageAccountName" -ForegroundColor Yellow
    
    az storage account create `
        --name $storageAccountName `
        --resource-group $webResourceGroup `
        --location $Location `
        --sku Standard_LRS `
        --kind StorageV2
    
    # Get connection string
    $connectionString = az storage account show-connection-string `
        --name $storageAccountName `
        --resource-group $webResourceGroup `
        --query connectionString `
        --output tsv
    
    Write-Host "Storage Account Connection String: $connectionString" -ForegroundColor Cyan
}

# Output deployment information
Write-Host "\n=== Deployment Information ===" -ForegroundColor Green
Write-Host "Web App URL: https://$($envConfig.web_app.app_name).azurewebsites.net" -ForegroundColor Cyan
Write-Host "Agent App URL: https://$($envConfig.agent_app.app_name).azurewebsites.net" -ForegroundColor Cyan
Write-Host "\nTo deploy your application, use one of the following methods:" -ForegroundColor Yellow
Write-Host "1. GitHub Actions (recommended): Push to the corresponding branch" -ForegroundColor White
Write-Host "2. Azure DevOps: Use the azure-deploy.yml pipeline" -ForegroundColor White
Write-Host "3. Manual deployment: Use 'az webapp deployment source config' command" -ForegroundColor White

Write-Host "\n=== Next Steps ===" -ForegroundColor Green
Write-Host "1. Update your GitHub repository secrets with the publish profiles" -ForegroundColor White
Write-Host "2. Replace placeholder values (\${...}) in environment variables with actual values" -ForegroundColor White
Write-Host "3. Configure your GitHub App settings with the new URLs" -ForegroundColor White
Write-Host "4. Test the deployment by pushing to the appropriate branch" -ForegroundColor White

Write-Host "\nSetup completed successfully!" -ForegroundColor Green