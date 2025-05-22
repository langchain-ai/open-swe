# Always start from the base E2B image
FROM e2bdev/code-interpreter:latest

# Ensure git is installed
RUN apt-get update && apt-get install -y git curl ripgrep

# Set the working directory
WORKDIR /app

# Download and install nvm, then Node.js, then Yarn
ENV NVM_DIR /root/.nvm
ENV NODE_VERSION 22
RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash \
    && . "$NVM_DIR/nvm.sh" \
    && nvm install "$NODE_VERSION" \
    && nvm alias default "$NODE_VERSION" \
    && nvm use default \
    && corepack enable yarn

# Add NVM's bin to the PATH for subsequent commands
ENV PATH $NVM_DIR/versions/node/v$NODE_VERSION/bin:$PATH
