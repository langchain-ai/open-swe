FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*
COPY warehouse /usr/local/bin/warehouse
COPY project/ /app/
COPY generate.py /tmp/generate.py
WORKDIR /app
RUN chmod +x /usr/local/bin/warehouse && mkdir -p /opt/warehouse \
    && python3 /tmp/generate.py /app /opt/warehouse/warehouse.db && rm /tmp/generate.py \
    && chmod 444 /opt/warehouse/warehouse.db \
    && printf 'exports/\n' > .gitignore \
    && git init -q && git add -A \
    && git -c user.name=eval -c user.email=eval@example.com commit -qm init
