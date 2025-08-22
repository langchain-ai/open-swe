# syntax=docker/dockerfile:1
FROM node:18-bullseye

# Enable Corepack to use the pinned Yarn version from packageManager fields
RUN corepack enable

# Set working directory at repo root to install monorepo deps once
WORKDIR /app

# Copy only package manifests first for better layer caching
COPY package.json yarn.lock turbo.json ./
COPY apps/open-swe/package.json apps/open-swe/package.json
COPY apps/web/package.json apps/web/package.json
COPY packages/shared/package.json packages/shared/package.json

# Now copy the rest of the repository
COPY . .

# Install dependencies for all workspaces with the repo-pinned Yarn version
RUN yarn install --immutable

# Expose dev server port for the LangGraph agent
EXPOSE 2024

# Environment can be overridden at runtime
ENV PORT=2024

# Default command runs the dev server for the agent
WORKDIR /app/apps/open-swe
CMD ["yarn", "dev"]


