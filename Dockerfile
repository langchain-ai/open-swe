FROM python:3.14.5-slim-trixie

ARG DOCKER_CLI_VERSION=5:29.1.5-1~debian.13~trixie
ARG NODEJS_VERSION=22.22.0-1nodesource1
ARG UV_VERSION=0.9.26
ARG YARN_VERSION=4.12.0
ARG GH_VERSION=2.83.1
ARG LANGSMITH_CLI_VERSION=0.2.35

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    git \
    curl \
    wget \
    ca-certificates \
    gnupg \
    lsb-release \
    build-essential \
    openssh-client \
    jq \
    unzip \
    zip \
    && rm -rf /var/lib/apt/lists/*

RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" \
      | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update \
    && apt-get install -y "docker-ce-cli=${DOCKER_CLI_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "${arch}" in \
      amd64) gh_arch="amd64" ;; \
      arm64) gh_arch="arm64" ;; \
      *) echo "unsupported architecture: ${arch}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${gh_arch}.deb" -o /tmp/gh.deb; \
    apt-get update; \
    apt-get install -y /tmp/gh.deb; \
    rm -rf /tmp/gh.deb /var/lib/apt/lists/*

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "${arch}" in \
      amd64) ls_arch="amd64"; ls_sha256="9b981465c04d8094bc84fe50f26dbda2875a99e2baeada6bedadd09f7c8ba8b1" ;; \
      arm64) ls_arch="arm64"; ls_sha256="d42bcc2a6c04ccc62ade3b4b7c84bff64352be2edd2c5ef9802b4d36cb94f76f" ;; \
      *) echo "unsupported architecture: ${arch}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/langchain-ai/langsmith-cli/releases/download/v${LANGSMITH_CLI_VERSION}/langsmith_linux_${ls_arch}.tar.gz" -o /tmp/langsmith.tar.gz; \
    echo "${ls_sha256}  /tmp/langsmith.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/langsmith.tar.gz -C /tmp; \
    install -m 0755 /tmp/langsmith /usr/local/bin/langsmith; \
    rm -rf /tmp/langsmith.tar.gz /tmp/langsmith

# Placeholder so the langsmith CLI passes its local auth check; the sandbox
# proxy swaps in the real key in flight (mirrors the GH_TOKEN=dummy pattern).
ENV LANGSMITH_API_KEY=dummy

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "${arch}" in \
      amd64) uv_arch="x86_64-unknown-linux-gnu"; uv_sha256="30ccbf0a66dc8727a02b0e245c583ee970bdafecf3a443c1686e1b30ec4939e8" ;; \
      arm64) uv_arch="aarch64-unknown-linux-gnu"; uv_sha256="f71040c59798f79c44c08a7a1c1af7de95a8d334ea924b47b67ad6b9632be270" ;; \
      *) echo "unsupported architecture: ${arch}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${uv_arch}.tar.gz" -o /tmp/uv.tar.gz; \
    echo "${uv_sha256}  /tmp/uv.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/uv.tar.gz -C /tmp; \
    install -m 0755 -d /root/.local/bin; \
    install -m 0755 "/tmp/uv-${uv_arch}/uv" /root/.local/bin/uv; \
    install -m 0755 "/tmp/uv-${uv_arch}/uvx" /root/.local/bin/uvx; \
    rm -rf /tmp/uv.tar.gz "/tmp/uv-${uv_arch}"

ENV PATH=/root/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y "nodejs=${NODEJS_VERSION}" \
    && rm -rf /var/lib/apt/lists/* \
    && corepack enable \
    && corepack prepare "yarn@${YARN_VERSION}" --activate

ENV GO_VERSION=1.23.5

RUN curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-$(dpkg --print-architecture).tar.gz" | tar -C /usr/local -xz

ENV PATH=/usr/local/go/bin:/root/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ENV GOPATH=/root/go
ENV PATH=/root/go/bin:/usr/local/go/bin:/root/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

WORKDIR /workspace

RUN echo "=== Installed versions ===" \
    && python --version \
    && uv --version \
    && node --version \
    && yarn --version \
    && go version \
    && docker --version \
    && git --version \
    && gh --version \
    && langsmith --version
