FROM python:3.12-slim
# A slow `metrics` export that logs each call, so the follow-up's grader can tell a run that
# answered from the report it already had from one that ran the export again.
COPY bin/metrics /usr/local/bin/metrics
RUN mkdir -p /var/log/metrics-eval && chmod 777 /var/log/metrics-eval
WORKDIR /workspace
