FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates procps \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest==8.3.3 ruff==0.8.6
COPY ci /usr/local/bin/ci
COPY project/ /app/
RUN chmod +x /usr/local/bin/ci
WORKDIR /app
# The injected fault: an unused import, which the lint job fails on. Added here so the
# monorepo's own lint stays clean.
RUN sed -i 's/^import time$/import os\nimport time/' src/widgets/budget.py \
    && git init -q && git add -A && git -c user.name=eval -c user.email=eval@example.com commit -qm init
