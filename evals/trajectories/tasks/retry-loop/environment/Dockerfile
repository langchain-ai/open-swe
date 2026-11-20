FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*
COPY threadctl /usr/local/bin/threadctl
COPY threads.json /var/lib/threadctl/threads.json
COPY make_repo.sh /tmp/make_repo.sh
RUN chmod +x /usr/local/bin/threadctl && mkdir -p /app/src /app/tests /app/docs \
    && sh /tmp/make_repo.sh && rm /tmp/make_repo.sh
WORKDIR /app
