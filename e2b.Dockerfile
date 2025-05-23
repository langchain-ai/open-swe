# Always start from the base E2B image
FROM e2bdev/code-interpreter:latest

# Ensure git is installed
RUN apt-get update && apt-get install -y git curl ripgrep

# Set the working directory
WORKDIR /app
