# Always start from the base E2B image
FROM e2bdev/code-interpreter:latest

# Ensure git is installed
RUN apt-get update && apt-get install -y git curl

# Set the working directory
WORKDIR /app

# Ensure Python and pip are installed
RUN apt-get install -y python3 python3-pip python3-venv

# Upgrade pip
RUN python3 -m pip install --upgrade pip

# Install common Python development tools
RUN python3 -m pip install pytest black isort mypy

# Set Python 3 as the default python
RUN ln -sf /usr/bin/python3 /usr/bin/python
