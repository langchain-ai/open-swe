FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends make git curl ca-certificates procps \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest==8.3.3
COPY project/ /app/
WORKDIR /app
RUN git init -q && git add -A && git -c user.name=eval -c user.email=eval@example.com commit -qm init
