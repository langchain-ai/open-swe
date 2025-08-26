#!/bin/bash

# Azure App Service startup script for Next.js application
# This script ensures proper initialization of the web application

echo "Starting Agent Mojo Web Application..."

# Set NODE_ENV if not already set
if [ -z "$NODE_ENV" ]; then
    export NODE_ENV=production
fi

echo "NODE_ENV: $NODE_ENV"
echo "Current directory: $(pwd)"
echo "Node version: $(node --version)"
echo "NPM version: $(npm --version)"

# Navigate to the web app directory
cd /home/site/wwwroot/apps/web

# Check if node_modules exists, if not install dependencies
if [ ! -d "node_modules" ]; then
    echo "Installing dependencies..."
    npm install --production
fi

# Check if .next directory exists, if not build the application
if [ ! -d ".next" ]; then
    echo "Building Next.js application..."
    npm run build
fi

# Start the application
echo "Starting Next.js server..."
exec npm start