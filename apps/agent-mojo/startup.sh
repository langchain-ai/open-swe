#!/bin/bash

# Azure App Service startup script for Agent Mojo application
# This script ensures proper initialization of the agent application

echo "Starting Agent Mojo Agent Application..."

# Set NODE_ENV if not already set
if [ -z "$NODE_ENV" ]; then
    export NODE_ENV=production
fi

echo "NODE_ENV: $NODE_ENV"
echo "Current directory: $(pwd)"
echo "Node version: $(node --version)"
echo "NPM version: $(npm --version)"

# Navigate to the agent app directory
cd /home/site/wwwroot/apps/agent-mojo

# Check if node_modules exists, if not install dependencies
if [ ! -d "node_modules" ]; then
    echo "Installing dependencies..."
    npm install --production
fi

# Check if dist directory exists, if not build the application
if [ ! -d "dist" ]; then
    echo "Building agent application..."
    npm run build
fi

# Start the application based on environment
if [ "$NODE_ENV" = "production" ]; then
    echo "Starting agent in production mode..."
    exec npm start
else
    echo "Starting agent in development mode..."
    exec npm run dev
fi