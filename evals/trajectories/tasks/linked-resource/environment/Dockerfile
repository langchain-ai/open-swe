FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep curl ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*
COPY bin/ /usr/local/lib/apps-eval/
COPY generate.py /tmp/generate.py
# The deployed app the user links, an older deploy, and the apps monorepo with an
# unreleased cap and vendored decoys. Wrappers ahead of /usr/bin on PATH log every grep,
# rg, find and `git grep`, so the grader can tell a run that searched before answering.
RUN python3 /tmp/generate.py && rm /tmp/generate.py \
    && for tool in grep rg find; do cp /usr/local/lib/apps-eval/search /usr/local/bin/$tool; done \
    && cp /usr/local/lib/apps-eval/git /usr/local/bin/git \
    && mkdir -p /var/log/apps-eval && chmod 777 /var/log/apps-eval
WORKDIR /workspace
