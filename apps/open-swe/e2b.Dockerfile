# Always start from the base E2B image
FROM e2bdev/code-interpreter:latest

# Install dependencies in a single layer
RUN apt-get update && apt-get install -y \
    git \
    curl \
    ripgrep \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (LTS version) via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Update corepack to latest version (to avoid outdated signature issues)
RUN npm install -g corepack@latest

# Enable corepack for yarn and pnpm (creates shims)
RUN corepack enable yarn pnpm

# Pre-download specific versions to avoid download on first use
RUN corepack prepare yarn@stable --activate \
    && corepack prepare pnpm@latest --activate

# Set the working directory
WORKDIR /app