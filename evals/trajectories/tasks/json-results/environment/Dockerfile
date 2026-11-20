FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*
COPY evalctl /usr/local/bin/evalctl
COPY generate.py /tmp/generate.py
RUN chmod +x /usr/local/bin/evalctl \
    && python3 /tmp/generate.py /var/lib/evalctl && rm /tmp/generate.py
WORKDIR /app
