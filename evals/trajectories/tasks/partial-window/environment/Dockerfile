FROM python:3.12-slim
# A routing experiment whose last day looks balanced and whose first two days don't, behind
# a counts tool that answers at most 24h per request.
COPY bin/routing /usr/local/bin/routing
RUN mkdir -p /var/lib/routing /var/log/routing-eval && chmod 777 /var/lib/routing /var/log/routing-eval
WORKDIR /workspace
