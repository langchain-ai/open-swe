FROM node:22-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep curl ca-certificates procps python3 \
    && rm -rf /var/lib/apt/lists/*
COPY project/ /app/
COPY generate.py /tmp/generate.py
WORKDIR /app
# Vendored dependencies and a large generated review patch, as in the traced repo: a
# whole-tree `grep -r` has to read all of it, while `rg` and `git grep` skip it.
RUN python3 /tmp/generate.py /app && rm /tmp/generate.py \
    && printf 'node_modules/\n*.patch\n' > .gitignore \
    && git init -q && git add -A \
    && git -c user.name=eval -c user.email=eval@example.com commit -qm init
