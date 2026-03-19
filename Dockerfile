FROM python:3.12.12-slim-trixie

ARG DOCKER_CLI_VERSION=5:29.1.5-1~debian.13~trixie
ARG NODEJS_VERSION=22.22.0-1nodesource1
ARG UV_VERSION=0.9.26
ARG YARN_VERSION=4.12.0

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
    && git --version
