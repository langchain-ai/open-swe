# Docker Sandbox Integration

This guide explains how to use Docker containers as sandbox environments for Open SWE agents. Docker sandboxes provide isolation while being easy to set up locally without requiring external cloud services.

## Overview

The Docker sandbox integration allows you to run agent tasks in isolated Docker containers on your local machine or any Docker host. This is ideal for:

- **Local development**: Test agent behavior with isolation without cloud services
- **Self-hosted deployments**: Run agents on your own infrastructure
- **CI/CD pipelines**: Automated testing and validation
- **Cost-sensitive environments**: No per-minute cloud sandbox costs

## Requirements

1. **Docker**: Install Docker Desktop (macOS/Windows) or Docker Engine (Linux)
2. **Python docker package**: Install with `pip install docker`
3. **Permissions**: Ensure you can create and manage Docker containers

## Quick Start

### 1. Install the Docker Python Package

```bash
pip install docker
```

### 2. Set Environment Variables

```bash
export SANDBOX_TYPE=docker
export DOCKER_SANDBOX_IMAGE=python:3.11-slim
```

### 3. Use in Your Agent

The Docker sandbox will be automatically used when `SANDBOX_TYPE=docker`:

```python
from agent.utils.sandbox import create_sandbox

# Create a new sandbox container
sandbox = create_sandbox()

# Execute commands
result = sandbox.execute("pip list")
print(result.output)

# Write files
sandbox.write("/workspace/hello.py", 'print("Hello, World!")')

# Run the script
result = sandbox.execute("python hello.py")
print(result.output)  # Hello, World!
```

## Configuration

Configure the Docker sandbox using environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `DOCKER_SANDBOX_IMAGE` | Base Docker image | `python:3.11-slim` |
| `DOCKER_SANDBOX_WORK_DIR` | Working directory in container | `/workspace` |
| `DOCKER_SANDBOX_MEMORY` | Memory limit in bytes | `2147483648` (2GB) |
| `DOCKER_SANDBOX_CPU_PERIOD` | CPU period for limiting | `100000` |
| `DOCKER_SANDBOX_CPU_QUOTA` | CPU quota (period==quota = 1 CPU) | `100000` |
| `DOCKER_SANDBOX_TIMEOUT` | Default command timeout (seconds) | `300` |
| `DOCKER_SANDBOX_NETWORK` | Network mode | `bridge` |
| `DOCKER_SANDBOX_CONTAINER_PREFIX` | Container name prefix | `open-swe-sandbox` |
| `DOCKER_SANDBOX_VOLUMES` | Volume mounts (JSON) | (none) |
| `DOCKER_SANDBOX_SECURITY_OPTS` | Security options (JSON) | (none) |

### Examples

#### Basic Configuration

```bash
export SANDBOX_TYPE=docker
export DOCKER_SANDBOX_IMAGE=python:3.12-slim
export DOCKER_SANDBOX_MEMORY=4294967296  # 4GB
```

#### Custom Image with Pre-installed Tools

Create a custom Dockerfile:

```dockerfile
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Pre-install common Python packages
RUN pip install --no-cache-dir \
    pytest \
    black \
    ruff \
    mypy

# Set up a non-root user (optional, for security)
RUN useradd -m -s /bin/bash agent
USER agent
WORKDIR /workspace
```

Build and use the image:

```bash
docker build -t my-org/open-swe-base:latest .
export DOCKER_SANDBOX_IMAGE=my-org/open-swe-base:latest
```

#### Volume Mounts for Persistent Storage

Mount host directories into the container:

```bash
# Mount a cache directory for pip/npm
export DOCKER_SANDBOX_VOLUMES='{
    "/host/cache/pip": {"bind": "/root/.cache/pip", "mode": "rw"},
    "/host/cache/npm": {"bind": "/root/.npm", "mode": "rw"}
}'
```

#### Network Isolation

Disable network access for sensitive operations:

```bash
export DOCKER_SANDBOX_NETWORK=none
```

#### Enhanced Security

Apply Docker security options:

```bash
export DOCKER_SANDBOX_SECURITY_OPTS='["no-new-privileges", "seccomp=unconfined"]'
```

## Resource Limits

### Memory Limits

Set memory limits to prevent runaway processes:

```bash
# 4GB memory limit
export DOCKER_SANDBOX_MEMORY=4294967296

# 8GB memory limit
export DOCKER_SANDBOX_MEMORY=8589934592
```

### CPU Limits

Control CPU usage:

```bash
# Limit to 1 CPU (period == quota)
export DOCKER_SANDBOX_CPU_PERIOD=100000
export DOCKER_SANDBOX_CPU_QUOTA=100000

# Limit to 2 CPUs
export DOCKER_SANDBOX_CPU_PERIOD=100000
export DOCKER_SANDBOX_CPU_QUOTA=200000

# Limit to 0.5 CPU
export DOCKER_SANDBOX_CPU_PERIOD=100000
export DOCKER_SANDBOX_CPU_QUOTA=50000
```

## Advanced Usage

### Reconnecting to Existing Containers

Containers persist across agent invocations. Reconnect using the container ID:

```python
# First run creates a container
sandbox1 = create_sandbox()
container_id = sandbox1.id
print(f"Container ID: {container_id}")

# Later, reconnect to the same container
sandbox2 = create_sandbox(sandbox_id=container_id)
# The workspace state is preserved
```

### Programmatic Container Management

```python
from agent.integrations.docker import (
    DockerSandboxProvider,
    list_docker_sandboxes,
    cleanup_all_docker_sandboxes,
)

# List all Open SWE sandbox containers
containers = list_docker_sandboxes()
for c in containers:
    print(f"  - {c['name']}: {c['status']} ({c['image']})")

# Clean up all sandbox containers
removed = cleanup_all_docker_sandboxes()
print(f"Removed {removed} containers")
```

### Custom Provider Configuration

```python
from agent.integrations.docker import DockerSandboxProvider, DockerSandboxBackend

# Create a provider with custom configuration
provider = DockerSandboxProvider(config={
    "image": "my-org/agent-env:latest",
    "work_dir": "/app",
    "memory": 8 * 1024 * 1024 * 1024,  # 8GB
    "cpu_period": 100000,
    "cpu_quota": 200000,  # 2 CPUs
    "timeout": 600,  # 10 minutes
    "network": "bridge",
})

# Create a sandbox
sandbox = provider.get_or_create()

# Use the sandbox
result = sandbox.execute("python --version")
print(result.output)

# Clean up when done
provider.delete(sandbox_id=sandbox.id)
```

## Comparison with Other Sandboxes

| Feature | Docker | Local | Modal/Daytona/Runloop | LangSmith |
|---------|--------|-------|----------------------|-----------|
| **Isolation** | Container | None | Cloud sandbox | Cloud sandbox |
| **Setup** | Docker required | None | API key | API key |
| **Cost** | Free (self-hosted) | Free | Per-minute | Per-minute |
| **Persistence** | Optional | N/A | Yes | Yes |
| **Network** | Configurable | Full access | Configurable | Configurable |
| **Scalability** | Limited by host | N/A | Auto-scale | Auto-scale |

## Security Considerations

### Container Isolation

Docker containers provide process-level isolation, but they share the host kernel. For sensitive workloads:

1. **Use minimal images**: Start with `slim` or `alpine` variants
2. **Disable network**: Set `DOCKER_SANDBOX_NETWORK=none` when network access isn't needed
3. **Apply security options**: Use `no-new-privileges` and seccomp profiles
4. **Run as non-root**: Create a non-root user in your Dockerfile

### Example Secure Configuration

```dockerfile
# Dockerfile
FROM python:3.11-slim

# Create non-root user
RUN useradd -m -s /bin/bash agent

# Install dependencies as root
RUN pip install --no-cache-dir pytest black ruff

# Switch to non-root user
USER agent
WORKDIR /workspace
```

```bash
# Environment
export DOCKER_SANDBOX_IMAGE=my-org/secure-agent:latest
export DOCKER_SANDBOX_NETWORK=none
export DOCKER_SANDBOX_SECURITY_OPTS='["no-new-privileges"]'
```

## Troubleshooting

### Docker Not Running

```
RuntimeError: Failed to connect to Docker. Ensure Docker is running
```

**Solution**: Start Docker Desktop or the Docker daemon.

### Permission Denied

```
docker.errors.DockerException: Error while fetching server API version
```

**Solution**: 
- On Linux, add your user to the `docker` group: `sudo usermod -aG docker $USER`
- On macOS/Windows, ensure Docker Desktop is running and you have the necessary permissions

### Image Pull Timeout

```
RuntimeError: Failed to create Docker container: ... timeout
```

**Solution**: 
- Check your network connection
- Pre-pull the image: `docker pull python:3.11-slim`
- Use a mirror or local registry

### Container Out of Memory

```
Exit code: 137
```

**Solution**: Increase the memory limit:
```bash
export DOCKER_SANDBOX_MEMORY=8589934592  # 8GB
```

## Best Practices

1. **Pre-build images**: Create custom images with all required tools to reduce setup time
2. **Set resource limits**: Always configure memory and CPU limits
3. **Clean up containers**: Remove containers when done to free resources
4. **Use volume mounts**: Cache dependencies between runs with volume mounts
5. **Monitor container usage**: Use `docker stats` to monitor resource usage
6. **Pin image versions**: Use specific image tags instead of `latest` for reproducibility

## Integration with Open SWE

To use Docker sandboxes with Open SWE:

1. Set the sandbox type:
   ```bash
   export SANDBOX_TYPE=docker
   ```

2. Configure your preferred image:
   ```bash
   export DOCKER_SANDBOX_IMAGE=my-org/open-swe-base:v1.0
   ```

3. Start the Open SWE agent:
   ```bash
   langgraph dev
   ```

The agent will automatically use Docker containers for all sandbox operations.