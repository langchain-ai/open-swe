# syntax=docker/dockerfile:1
FROM node:18-bullseye

# Enable Corepack to use the pinned Yarn version
RUN corepack enable

# Install dependencies from the monorepo root for caching efficiency
WORKDIR /app

# Copy only manifests first to maximize Docker layer caching
COPY package.json yarn.lock turbo.json ./
COPY apps/open-swe/package.json apps/open-swe/package.json
COPY apps/web/package.json apps/web/package.json
COPY packages/shared/package.json packages/shared/package.json

# Copy the full repository
COPY . .

# Install all workspace dependencies
RUN yarn install --immutable

# Expose Next.js dev server port
EXPOSE 3000

# Ensure Next dev binds to all interfaces in Docker
ENV HOSTNAME=0.0.0.0
ENV PORT=3000

# Run the web app dev server
WORKDIR /app/apps/web
CMD ["yarn", "dev", "-H", "0.0.0.0"]


