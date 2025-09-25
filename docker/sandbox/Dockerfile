# syntax=docker/dockerfile:1
FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG USERNAME=dev
ARG USER_UID=1000
ARG USER_GID=1000

# Install dependencies and tools
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        software-properties-common \
        unzip \
        build-essential \
        git \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-distutils \
        python3.11-venv \
        python3.11-dev \
        openjdk-21-jdk \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get install -y --no-install-recommends maven gradle \
    && rm -rf /var/lib/apt/lists/*

# Install pip for Python 3.11 and Python tooling
RUN curl -fsSL https://bootstrap.pypa.io/get-pip.py | python3.11 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 \
    && python -m pip install --no-cache-dir --upgrade pip setuptools wheel pytest

# Enable Corepack and install JavaScript tooling
RUN corepack enable \
    && corepack prepare pnpm@latest --activate \
    && npm install -g ts-node eslint

# Create non-root user with /workspace home directory
RUN groupadd --gid ${USER_GID} ${USERNAME} \
    && useradd --uid ${USER_UID} --gid ${USER_GID} --create-home --home-dir /workspace --shell /bin/bash ${USERNAME}

ENV PATH="/workspace/.local/bin:${PATH}"
WORKDIR /workspace
USER ${USERNAME}

CMD ["bash"]
