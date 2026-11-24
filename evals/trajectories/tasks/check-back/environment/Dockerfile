FROM python:3.12-slim
# A six-minute export and a `sleep` that logs each call, so the grader can tell a run that
# waited for the export's wakeup from one that slept or polled the turn away.
COPY bin/export-data bin/sleep /usr/local/bin/
RUN mkdir -p /var/lib/export /var/log/export-eval \
    && chmod 777 /var/lib/export /var/log/export-eval
WORKDIR /workspace
